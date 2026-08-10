"""
Streamlit control panel for the Bitcoin trading agent.

A thin UI over the modules already built in BitTradeAgent_updated.ipynb —
it imports directly from `src/` rather than duplicating any strategy, data,
or risk logic. Everything here is read/trigger only: viewing live state,
manually firing the two scheduled jobs (trading cycle, daily regime job),
running a backtest, and checking component health.

Run with:  streamlit run streamlit_app.py
"""

import io
import json
import contextlib
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from src.config.config_manager import load_config
from src.data.price_data import fetch_bitcoin_data, calculate_atr, get_latest_price_and_atr
from src.data.intraday_price_data import get_latest_price_and_intraday_atr
from src.ml.forecaster import forecast_next
from src.ml.llm_regime import classify_regime
from src.ml.threshold_adapter import read_overrides
from src.broker.paper_broker import get_position, set_initial_cash, TRADES_CSV, PORTFOLIO_JSON
from src.backtest.engine import BacktestEngine
from src.jobs import daily_regime_job
from src.notify.telegram import send_message
from src.notify.gmail_report import generate_weekly_report
import src.main as trading_cycle

# Categorical slots 1/2 + the reserved "critical" status color for STOP,
# since a stop-loss fill is an adverse event, not just a third series.
COLOR_BLUE = "#2a78d6"
COLOR_ORANGE = "#eb6834"
COLOR_CRITICAL = "#d03b3b"
COLOR_GOOD = "#0ca30c"
COLOR_MUTED = "#898781"
TRADE_TYPES = ["DCA", "SWING", "STOP"]
TRADE_COLORS = [COLOR_BLUE, COLOR_ORANGE, COLOR_CRITICAL]

st.set_page_config(page_title="Bitcoin Trading Agent", page_icon="₿", layout="wide")


def status_pill(label: str, healthy: bool) -> str:
    color = COLOR_GOOD if healthy else COLOR_CRITICAL
    dot = "●"
    return f'<span style="color:{color}; font-weight:600;">{dot} {label}</span>'


def redact(value: str) -> str:
    if not value:
        return "not set"
    return f"set ({value[:3]}…{value[-2:]})" if len(value) > 6 else "set"


@st.cache_resource
def get_config():
    return load_config()


@st.cache_data(ttl=30)
def get_daily_snapshot():
    return get_latest_price_and_atr()


@st.cache_data(ttl=30)
def get_intraday_snapshot():
    return get_latest_price_and_intraday_atr()


@st.cache_data(ttl=300)
def get_daily_candles(period: str):
    df = fetch_bitcoin_data(period=period)
    if df.empty:
        return df
    return df.rename(columns={"close": "Close", "high": "High", "low": "Low"})


def load_trades() -> pd.DataFrame:
    if not TRADES_CSV.exists():
        return pd.DataFrame(columns=["timestamp", "type", "price_usd", "amount_usd", "btc_amount", "notes"])
    df = pd.read_csv(TRADES_CSV)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def load_portfolio() -> dict:
    if not PORTFOLIO_JSON.exists():
        return {}
    with PORTFOLIO_JSON.open() as f:
        return json.load(f)


def build_equity_curve(trades_df: pd.DataFrame, initial_cash: float) -> pd.DataFrame:
    """Reconstruct portfolio value at each fill from trades.csv — priced at
    that fill's own execution price, not a continuous mark-to-market series
    (the live system doesn't persist one; see section 12's backtest caveats
    for the same limitation)."""
    if trades_df.empty:
        return pd.DataFrame(columns=["timestamp", "portfolio_value"])
    cash, btc = initial_cash, 0.0
    rows = []
    for _, tr in trades_df.sort_values("timestamp").iterrows():
        if tr["type"] == "BUY":
            cash -= tr["amount_usd"]
            btc += tr["btc_amount"]
        else:
            cash += tr["amount_usd"]
            btc -= tr["btc_amount"]
        rows.append({"timestamp": tr["timestamp"], "portfolio_value": cash + btc * tr["price_usd"]})
    return pd.DataFrame(rows)


cfg = get_config()

st.title("₿ Bitcoin Trading Agent")
st.caption(
    f"Mode: **{cfg.get('TRADING_MODE', 'hybrid')}** · Budget: ${float(cfg.get('BUDGET_USD', 0)):,.0f} "
    "· Paper trading only — no real orders are placed against Coinbase."
)

with st.sidebar:
    st.header("Config")
    st.caption("Loaded via `load_config()` — secrets are never shown, only whether they're set.")
    st.write(f"**LLM provider:** {cfg.get('LLM_PROVIDER') or 'disabled'}")
    st.write(f"**Coinbase key:** {redact(cfg.get('COINBASE_API_KEY', ''))}")
    st.write(f"**Telegram:** {redact(cfg.get('TELEGRAM_BOT_TOKEN', ''))}")
    st.write(f"**Gmail:** {redact(cfg.get('GMAIL_APP_PASSWORD', ''))}")
    st.divider()

    st.header("Utilities")
    if st.button("Seed paper portfolio", help="No-ops if the portfolio already has cash or BTC in it."):
        set_initial_cash(float(cfg.get("BUDGET_USD", 10000)))
        st.cache_data.clear()
        st.success("Seeded (or already funded — set_initial_cash only writes to a fresh portfolio).")

    if st.button("Send test Telegram message"):
        sent = send_message("Test message from the Streamlit control panel.")
        if sent:
            st.success("Sent.")
        else:
            st.warning("Not sent — check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env.")

    if st.button("\U0001f504 Refresh market data"):
        st.cache_data.clear()
        st.rerun()

tab_dashboard, tab_cycle, tab_regime, tab_backtest, tab_history, tab_health = st.tabs(
    ["Dashboard", "Trading Cycle", "Regime & Thresholds", "Backtest", "Trade History", "System Health"]
)

# ---------------------------------------------------------------- Dashboard
with tab_dashboard:
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Daily (regime path)")
        daily = get_daily_snapshot()
        if "error" in daily:
            st.error(daily["error"])
        else:
            c1, c2 = st.columns(2)
            c1.metric("BTC price", f"${daily['price']:,.2f}")
            c2.metric("Daily ATR(14)", f"${daily['atr']:,.2f}")
    with col2:
        st.subheader("Intraday (stop-sizing path)")
        intraday = get_intraday_snapshot()
        if "error" in intraday:
            st.error(intraday["error"])
        else:
            c1, c2 = st.columns(2)
            c1.metric("BTC price", f"${intraday['price']:,.2f}")
            c2.metric(f"Intraday ATR ({intraday.get('granularity', '')})", f"${intraday['atr']:,.2f}")

    st.divider()
    st.subheader("Paper portfolio")
    position = get_position()
    portfolio = load_portfolio()
    price_now = daily.get("price", 0) if "error" not in daily else 0
    current_value = position["cash_usd"] + position["btc"] * price_now
    initial_cash = float(portfolio.get("initial_cash", 0))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cash", f"${position['cash_usd']:,.2f}")
    c2.metric("BTC held", f"{position['btc']:.6f}")
    c3.metric("Portfolio value", f"${current_value:,.2f}")
    if initial_cash > 0:
        pnl_pct = (current_value - initial_cash) / initial_cash * 100
        c4.metric("Unrealized P&L", f"{pnl_pct:+.2f}%")
    else:
        c4.metric("Unrealized P&L", "—")

# ------------------------------------------------------------- Trading Cycle
with tab_cycle:
    st.subheader("Run one trading cycle (src/main.py → run_once())")
    st.caption(
        "Fetches intraday price/ATR, reads the latest runtime overrides, evaluates DCA + swing "
        "logic, and executes any resulting paper fills — exactly what the scheduler runs every "
        f"{cfg.get('DATA_FETCH_INTERVAL_MIN', 30)} minutes in production."
    )
    if st.button("▶️ Run trading cycle now", type="primary"):
        buf = io.StringIO()
        with st.spinner("Running..."):
            with contextlib.redirect_stdout(buf):
                trading_cycle.run_once()
        st.code(buf.getvalue() or "(no output)")
        st.cache_data.clear()

# --------------------------------------------------------- Regime & Thresholds
with tab_regime:
    st.subheader("Current runtime overrides (data/runtime_overrides.json)")
    st.caption("Written by the daily regime job — read by every trading cycle. Empty until the job has run once.")
    overrides = read_overrides()
    if overrides:
        st.json(overrides)
    else:
        st.info("No overrides written yet.")

    if st.button("\U0001f4c5 Run daily regime job now", type="primary"):
        with st.spinner("Fetching daily candles, forecasting, classifying regime..."):
            result = daily_regime_job.run(cfg)
        if result is None:
            st.error("Job skipped — no daily candle data available.")
        else:
            st.success("Overrides updated.")
            st.json(result)
        st.cache_data.clear()

    st.divider()
    st.subheader("Ad-hoc preview (doesn't write overrides)")
    if st.button("Preview forecast + LLM regime classification"):
        with st.spinner("Fetching daily candles..."):
            df = get_daily_candles("60d")
        if df.empty:
            st.error("No daily data available.")
        else:
            pred_return, pred_strength = forecast_next(df)
            c1, c2 = st.columns(2)
            c1.metric("Predicted return", f"{pred_return:+.4f}")
            c2.metric("Prediction strength", f"{pred_strength:.3f}")
            atr_series = calculate_atr(df, period=14)
            atr_val = float(atr_series.iloc[-1]) if not atr_series.empty else 0.0
            price_val = float(df["Close"].iloc[-1])
            recommendation = classify_regime(
                {
                    "pred_return": pred_return,
                    "pred_strength": pred_strength,
                    "atr_pct_of_price": round(atr_val / price_val, 5) if price_val else 0.0,
                },
                cfg,
            )
            st.write(f"**Regime:** {recommendation.regime} (confidence {recommendation.confidence:.2f}, source: {recommendation.source})")
            st.write(f"**Rationale:** {recommendation.rationale}")

# -------------------------------------------------------------------- Backtest
with tab_backtest:
    st.subheader("Backtest (src/backtest/engine.py)")
    st.caption(
        "Replays the strategy bar-by-bar over historical **daily** candles — see section 12's "
        "caveats in the notebook (daily-only ATR, fixed overrides for the whole run, no fees/slippage)."
    )

    with st.form("backtest_form"):
        c1, c2, c3 = st.columns(3)
        period = c1.selectbox("Lookback", ["30d", "60d", "90d", "180d"], index=1)
        initial_budget = c1.number_input("Initial budget (USD)", value=float(cfg.get("BUDGET_USD", 10000)), step=500.0)
        trading_mode = c1.selectbox("Trading mode", ["hybrid", "dca_only"], index=0)
        dca_amount = c2.number_input("DCA amount (USD)", value=float(cfg.get("DCA_AMOUNT_USD", 500)), step=50.0)
        dca_drop_pct = c2.number_input("DCA drop trigger (%)", value=float(cfg.get("DCA_DROP_PERCENT", 3.0)), step=0.5)
        min_interval_hours = c2.number_input("Min hours between DCA buys", value=int(cfg.get("DCA_MIN_INTERVAL_HOURS", 24)), step=1)
        max_drawdown_pct = c3.number_input("Max drawdown (%) before pause", value=float(cfg.get("MAX_DRAWDOWN_PCT", 25.0)), step=1.0)
        enable_swing = c3.checkbox("Enable swing entries", value=True)
        atr_k_stop = c3.number_input("ATR stop multiplier (k)", value=1.5, step=0.1, min_value=1.0, max_value=2.5)
        submitted = st.form_submit_button("▶️ Run backtest", type="primary")

    if submitted:
        with st.spinner("Fetching candles and running backtest..."):
            df_daily = get_daily_candles(period)
            if df_daily.empty:
                st.error("No daily data available for this lookback window.")
            else:
                df_bt = df_daily.copy()
                df_bt["ATR"] = calculate_atr(df_bt, period=14)
                df_bt = df_bt.set_index("start")

                engine = BacktestEngine(initial_budget=initial_budget, max_drawdown_pct=max_drawdown_pct, trading_mode=trading_mode)
                results = engine.run_backtest(
                    historical_data=df_bt,
                    dca_config={
                        "dca_amount_usd": dca_amount,
                        "dca_drop_percent": dca_drop_pct,
                        "min_interval_hours": min_interval_hours,
                    },
                    overrides={"enable_swing": enable_swing, "atr_k_stop": atr_k_stop},
                )

        if results and "error" not in results:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total return", f"{results['total_return_pct']:+.2f}%")
            c2.metric("Final value", f"${results['final_value']:,.2f}")
            c3.metric("Max drawdown", f"{results['max_drawdown']:.2f}%")
            c4.metric("Total trades", results["total_trades"])

            hist_df = pd.DataFrame(engine.portfolio_history)
            if not hist_df.empty:
                hist_df["timestamp"] = pd.to_datetime(hist_df["timestamp"])
                chart = (
                    alt.Chart(hist_df)
                    .mark_line(color=COLOR_BLUE, strokeWidth=2)
                    .encode(
                        x=alt.X("timestamp:T", title=None),
                        y=alt.Y("portfolio_value:Q", title="Portfolio value (USD)"),
                        tooltip=[alt.Tooltip("timestamp:T", title="Date"), alt.Tooltip("portfolio_value:Q", title="Value", format="$,.0f")],
                    )
                    .properties(height=280)
                    .interactive()
                )
                st.altair_chart(chart, width="stretch")

            counts_df = pd.DataFrame(
                {
                    "type": TRADE_TYPES,
                    "count": [results["dca_trades"], results["swing_trades"], results["stop_trades"]],
                }
            )
            bar = (
                alt.Chart(counts_df)
                .mark_bar(size=40)
                .encode(
                    x=alt.X("type:N", title=None, sort=TRADE_TYPES),
                    y=alt.Y("count:Q", title="Fills"),
                    color=alt.Color("type:N", scale=alt.Scale(domain=TRADE_TYPES, range=TRADE_COLORS), legend=None),
                    tooltip=["type", "count"],
                )
                .properties(height=220)
            )
            st.altair_chart(bar, width="stretch")
        elif results:
            st.error(results["error"])

# --------------------------------------------------------------- Trade History
with tab_history:
    st.subheader("Paper trade fills (data/trades.csv)")
    trades_df = load_trades()
    if trades_df.empty:
        st.info("No trades yet — run a trading cycle or a backtest first.")
    else:
        tag_filter = st.multiselect(
            "Filter by trade tag", sorted(trades_df["notes"].unique()), default=list(trades_df["notes"].unique())
        )
        filtered = trades_df[trades_df["notes"].isin(tag_filter)]
        st.dataframe(filtered.sort_values("timestamp", ascending=False), width="stretch", hide_index=True)

        portfolio = load_portfolio()
        initial_cash = float(portfolio.get("initial_cash", cfg.get("BUDGET_USD", 10000)))
        equity_df = build_equity_curve(trades_df, initial_cash)
        if not equity_df.empty:
            chart = (
                alt.Chart(equity_df)
                .mark_line(color=COLOR_BLUE, strokeWidth=2, point=alt.OverlayMarkDef(size=40))
                .encode(
                    x=alt.X("timestamp:T", title=None),
                    y=alt.Y("portfolio_value:Q", title="Portfolio value at each fill (USD)"),
                    tooltip=[alt.Tooltip("timestamp:T", title="Date"), alt.Tooltip("portfolio_value:Q", title="Value", format="$,.0f")],
                )
                .properties(height=280)
                .interactive()
            )
            st.altair_chart(chart, width="stretch")
            st.caption("Priced at each fill's own execution price — not a continuous mark-to-market series.")

    st.divider()
    st.subheader("Weekly report preview (src/notify/gmail_report.py)")
    if st.button("Generate weekly report preview"):
        st.text(generate_weekly_report())

# --------------------------------------------------------------- System Health
with tab_health:
    st.subheader("Component health check")
    st.caption("Mirrors the notebook's section 18 — one lightweight call per component.")

    if st.button("\U0001f50d Run health check", type="primary"):
        checks = {}

        try:
            market_data = get_latest_price_and_atr()
            checks["Daily data"] = "error" not in market_data
        except Exception:
            checks["Daily data"] = False

        try:
            intraday_data = get_latest_price_and_intraday_atr()
            checks["Intraday data"] = "error" not in intraday_data
        except Exception:
            checks["Intraday data"] = False

        try:
            df = get_daily_candles("60d")
            forecast_next(df)
            checks["ML forecasting"] = True
        except Exception:
            checks["ML forecasting"] = False

        try:
            classify_regime({"pred_return": 0.0, "pred_strength": 0.0}, cfg)
            checks["LLM regime"] = True
        except Exception:
            checks["LLM regime"] = False

        try:
            read_overrides()
            checks["Threshold adaptation"] = True
        except Exception:
            checks["Threshold adaptation"] = False

        try:
            get_position()
            checks["Paper broker"] = True
        except Exception:
            checks["Paper broker"] = False

        healthy = sum(checks.values())
        st.markdown(" &nbsp;&nbsp; ".join(status_pill(name, ok) for name, ok in checks.items()), unsafe_allow_html=True)
        st.write(f"**Overall: {healthy}/{len(checks)} components healthy**")
