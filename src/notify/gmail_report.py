import json
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd

from src.config.config_manager import load_config

# Fix vs. the original notebook: this previously pointed at
# "src/ml/runtime_overrides.json", which nothing ever wrote to —
# write_overrides() in threshold_adapter.py writes to "data/runtime_overrides.json".
# The ml_insight section below was silently empty in every report because
# of that path mismatch.
OVERRIDES_FILE = Path("data/runtime_overrides.json")


def calculate_weekly_metrics():
    """Calculate P&L and trade metrics for the last 7 days."""
    trades_file = Path("data/trades.csv")
    if not trades_file.exists():
        return {"error": "No trades data found"}

    df = pd.read_csv(trades_file)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    week_ago = datetime.now() - timedelta(days=7)
    weekly_trades = df[df['timestamp'] >= week_ago]

    if weekly_trades.empty:
        return {"error": "No trades in last 7 days"}

    total_trades = len(weekly_trades)
    dca_trades = len(weekly_trades[weekly_trades['notes'] == 'DCA'])
    # Fix vs. the original: swing trades are now correctly labeled "swing" /
    # "swing_stop" at the source (see main.py), so this match actually works.
    swing_trades = len(weekly_trades[weekly_trades['notes'].str.contains('swing', case=False, na=False)])

    portfolio_file = Path("data/portfolio.json")
    if portfolio_file.exists():
        with portfolio_file.open("r") as f:
            portfolio = json.load(f)
        current_btc = float(portfolio.get("btc", 0))
        current_cash = float(portfolio.get("cash_usd", 0))
    else:
        current_btc = 0
        current_cash = 0

    try:
        from src.data.price_data import get_latest_price_and_atr
        market = get_latest_price_and_atr()
        current_price = float(market.get("price", 0))
    except Exception:
        current_price = 0

    current_value = current_btc * current_price + current_cash

    # NOTE (unchanged limitation from the original notebook): computing an
    # accurate weekly P&L requires a daily portfolio-value snapshot to diff
    # against — this system doesn't persist one yet, so these stay at 0
    # rather than reporting a misleading number. See the review notes for
    # the cheapest fix (a once-daily portfolio value snapshot).
    weekly_pnl = 0
    weekly_pnl_pct = 0

    return {
        "total_trades": total_trades,
        "dca_trades": dca_trades,
        "swing_trades": swing_trades,
        "current_btc": current_btc,
        "current_value": current_value,
        "weekly_pnl": weekly_pnl,
        "weekly_pnl_pct": weekly_pnl_pct,
        "current_price": current_price,
    }


def generate_weekly_report():
    """Generate weekly report content."""
    metrics = calculate_weekly_metrics()

    if "error" in metrics:
        return f"Weekly Report Error: {metrics['error']}"

    strategy_insight = "No strategy insight available"
    if OVERRIDES_FILE.exists():
        try:
            with OVERRIDES_FILE.open("r") as f:
                overrides = json.load(f)
            regime = overrides.get("llm_regime")
            rationale = overrides.get("llm_rationale")
            if regime and rationale:
                strategy_insight = f"Regime: {regime}. {rationale}"
            elif overrides.get("enable_swing") is not None:
                strategy_insight = (
                    f"Rule-based thresholds active — atr_k_stop={overrides.get('atr_k_stop')}, "
                    f"dca_drop_percent={overrides.get('dca_drop_percent')}"
                )
        except Exception:
            pass

    report = f"""
Bitcoin Trading Agent - Weekly Report
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Portfolio Summary:
- Total BTC: {metrics['current_btc']:.6f}
- Current Value: ${metrics['current_value']:,.2f}
- BTC Price: ${metrics['current_price']:,.2f}

Weekly Activity:
- Total Trades: {metrics['total_trades']}
- DCA Trades: {metrics['dca_trades']}
- Swing Trades: {metrics['swing_trades']}

Strategy Insight:
{strategy_insight}

Note: This is a paper trading system. All trades are simulated.
    """
    return report.strip()


def send_weekly_report():
    """Send weekly report via Gmail."""
    cfg = load_config()

    if not cfg.get("GMAIL_USER") or not cfg.get("GMAIL_APP_PASSWORD"):
        print("Gmail credentials not configured")
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = cfg["GMAIL_USER"]
        msg['To'] = cfg["GMAIL_USER"]
        msg['Subject'] = f"Bitcoin Trading Agent - Weekly Report {datetime.now().strftime('%Y-%m-%d')}"

        report_content = generate_weekly_report()
        msg.attach(MIMEText(report_content, 'plain'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(cfg["GMAIL_USER"], cfg["GMAIL_APP_PASSWORD"])
        server.send_message(msg)
        server.quit()

        print("Weekly report sent successfully")
        return True

    except Exception as e:
        print(f"Failed to send weekly report: {e}")
        return False


if __name__ == "__main__":
    print(generate_weekly_report())
