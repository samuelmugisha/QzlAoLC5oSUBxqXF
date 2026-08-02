from datetime import datetime

from src.config.config_manager import load_config
from src.notify.telegram import send_message
from src.strategy.strategy_manager import StrategyManager
# Fix vs. the original notebook: use intraday ATR (30-minute candles) to
# size new swing stop-losses, since the trading cycle itself runs every
# 30 minutes — see src/data/intraday_price_data.py and the Option C
# discussion in the project notes. Daily-candle ATR/forecast is still
# used, separately, by src/jobs/daily_regime_job.py for regime
# classification, which is a genuinely slower-moving question.
from src.data.intraday_price_data import get_latest_price_and_intraday_atr
from src.broker.paper_broker import set_initial_cash, get_position, place_market_buy, place_market_sell
from src.ml.threshold_adapter import read_overrides


def run_once():
    cfg = load_config()
    market = get_latest_price_and_intraday_atr()
    if "error" in market:
        print(market["error"])
        return

    price = float(market["price"])
    print(f"Price now: ${price:,.2f}")

    # Initialize paper broker cash once — set_initial_cash() only writes
    # if the portfolio is still fresh (cash == 0 and btc == 0), so this
    # is safe to call every cycle without resetting an in-progress portfolio.
    set_initial_cash(float(cfg.get("BUDGET_USD", 10000)))

    overrides = read_overrides()

    manager = StrategyManager(
        budget_usd=float(cfg.get("BUDGET_USD", 10000)),
        dca_amount_usd=float(cfg.get("DCA_AMOUNT_USD", 500)),
        dca_drop_percent=float(overrides.get("dca_drop_percent", cfg.get("DCA_DROP_PERCENT", 3.0))),
        min_interval_hours=int(cfg.get("DCA_MIN_INTERVAL_HOURS", 24)),
        max_drawdown_pct=float(cfg.get("MAX_DRAWDOWN_PCT", 25.0)),
        trading_mode=cfg.get("TRADING_MODE", "hybrid"),
    )

    market_atr = float(market.get("atr", 0.0))
    hybrid = manager.evaluate_hybrid(
        current_price=price,
        atr_value=market_atr,
        now=datetime.now(),
        overrides=overrides,
        swing_amount_usd=float(cfg.get("SWING_AMOUNT_USD", cfg.get("DCA_AMOUNT_USD", 500))),
    )

    if hybrid.get("risk_pause"):
        print(f"RISK PAUSE: {hybrid.get('risk_message')}")
        return

    # DCA action
    if hybrid["dca"].get("should_buy"):
        filled = place_market_buy(price_usd=price, amount_usd=float(hybrid["dca"]["amount_usd"]), note="DCA")
        pos = get_position()
        msg = (
            f"DCA Buy: ${filled['amount_usd']:.2f} at ${filled['price_usd']:.2f}. "
            f"BTC +{filled['btc_amount']:.6f}. Portfolio: {pos['btc']:.6f} BTC, ${pos['cash_usd']:.2f} cash."
        )
        print(msg)
        send_message(msg)
    else:
        print(f"DCA Hold: {hybrid['dca'].get('reason')}")

    # Swing open (only if hybrid mode and enabled)
    if hybrid.get("swing_open"):
        tr = hybrid["swing_open"]
        # Fix vs. the original notebook: note="swing" was missing here,
        # so every swing entry was logged as "DCA" in trades.csv and the
        # weekly report's swing-trade count was always 0.
        filled = place_market_buy(price_usd=price, amount_usd=float(tr["amount_usd"]), note="swing")
        manager.record_swing_open({**tr, **filled})
        pos = get_position()
        # Fix vs. the original notebook: the second line of this message
        # was missing its f-string prefix, so the literal text
        # "${tr['stop_loss']:.2f} (k={tr['atr_k']:.2f})" was sent instead
        # of the interpolated values.
        msg = (
            f"Swing Open: ${filled['amount_usd']:.2f} at ${filled['price_usd']:.2f}, "
            f"Stop ${tr['stop_loss']:.2f} (k={tr['atr_k']:.2f}). Portfolio: {pos['btc']:.6f} BTC."
        )
        print(msg)
        send_message(msg)

    # Swing stop closures
    for tr in hybrid.get("swing_closures", []):
        btc = float(tr.get("btc_amount", 0))
        if btc > 0:
            # Fix vs. the original notebook: note="swing_stop" was missing here too.
            filled = place_market_sell(price_usd=price, btc_amount=btc, note="swing_stop")
            manager.record_swing_close(tr.get("trade_id"))
            msg = (
                f"Swing Stop Hit: Sold {btc:.6f} BTC at ${price:.2f}. "
                f"Entry ${tr['entry_price']:.2f}, Stop ${tr['stop_loss']:.2f}."
            )
            print(msg)
            send_message(msg)


if __name__ == "__main__":
    run_once()
