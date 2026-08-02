import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("data")
TRADES_CSV = DATA_DIR / "trades.csv"
PORTFOLIO_JSON = DATA_DIR / "portfolio.json"


def _ensure_storage():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TRADES_CSV.exists():
        TRADES_CSV.write_text("timestamp,type,price_usd,amount_usd,btc_amount,notes\n", encoding="utf-8")
    if not PORTFOLIO_JSON.exists():
        PORTFOLIO_JSON.write_text(
            json.dumps({"cash_usd": 0.0, "btc": 0.0, "initial_cash": 0.0}, indent=2), encoding="utf-8"
        )


def _read_portfolio():
    _ensure_storage()
    with PORTFOLIO_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_portfolio(state):
    with PORTFOLIO_JSON.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def set_initial_cash(budget_usd):
    """Initialize portfolio cash — only if the portfolio is fresh (both
    cash and BTC are still zero). Safe to call every cycle without
    resetting an in-progress portfolio."""
    state = _read_portfolio()
    current_cash = float(state.get("cash_usd", 0.0))
    current_btc = float(state.get("btc", 0.0))
    if current_cash == 0.0 and current_btc == 0.0:
        state["cash_usd"] = float(budget_usd)
        state["initial_cash"] = float(budget_usd)
        _write_portfolio(state)


def get_position():
    state = _read_portfolio()
    return {"cash_usd": float(state.get("cash_usd", 0.0)), "btc": float(state.get("btc", 0.0))}


def place_market_buy(price_usd, amount_usd, note="DCA"):
    _ensure_storage()
    state = _read_portfolio()
    if amount_usd > state.get("cash_usd", 0.0):
        raise ValueError("Insufficient cash for buy")
    btc_amount = amount_usd / price_usd if price_usd > 0 else 0.0
    state["cash_usd"] = float(state.get("cash_usd", 0.0)) - float(amount_usd)
    state["btc"] = float(state.get("btc", 0.0)) + float(btc_amount)
    _write_portfolio(state)
    with TRADES_CSV.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()},BUY,{price_usd:.2f},{amount_usd:.2f},{btc_amount:.8f},{note}\n")
    return {"price_usd": price_usd, "amount_usd": amount_usd, "btc_amount": btc_amount}


def place_market_sell(price_usd, btc_amount, note="SELL"):
    _ensure_storage()
    state = _read_portfolio()
    if btc_amount > state.get("btc", 0.0):
        raise ValueError("Insufficient BTC for sell")
    amount_usd = btc_amount * price_usd
    state["cash_usd"] = float(state.get("cash_usd", 0.0)) + float(amount_usd)
    state["btc"] = float(state.get("btc", 0.0)) - float(btc_amount)
    _write_portfolio(state)
    with TRADES_CSV.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()},SELL,{price_usd:.2f},{amount_usd:.2f},{btc_amount:.8f},{note}\n")
    return {"price_usd": price_usd, "amount_usd": amount_usd, "btc_amount": btc_amount}
