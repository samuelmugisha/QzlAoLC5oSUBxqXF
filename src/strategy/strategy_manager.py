from uuid import uuid4
from datetime import datetime
from pathlib import Path
import json


class StrategyManager:
    """Minimal orchestrator for DCA-first paper trading.

    Extensible to include ATR swing and guardrails.
    """

    def __init__(
        self,
        budget_usd,
        dca_amount_usd,
        dca_drop_percent,
        min_interval_hours=24,
        max_drawdown_pct=25.0,
        trading_mode="hybrid",
    ):
        # Local import to avoid a circular import between strategy_manager
        # and dca_strategy at module load time.
        from src.strategy.dca_strategy import DCAStrategy

        self.dca = DCAStrategy(
            budget_usd=budget_usd,
            dca_amount_usd=dca_amount_usd,
            dca_drop_percent=dca_drop_percent,
            min_interval_hours=min_interval_hours,
        )
        self._last_close_price = None
        self._active_trades_path = Path("data/active_trades.json")
        self.max_drawdown_pct = float(max_drawdown_pct)
        self._risk_alert_sent = False
        self.trading_mode = trading_mode.lower()

    def _load_active_trades(self):
        self._active_trades_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._active_trades_path.exists():
            return []
        try:
            with self._active_trades_path.open("r", encoding="utf-8") as f:
                return json.load(f) or []
        except Exception:
            return []

    def _save_active_trades(self, trades):
        self._active_trades_path.parent.mkdir(parents=True, exist_ok=True)
        with self._active_trades_path.open("w", encoding="utf-8") as f:
            json.dump(trades, f, indent=2, default=str)

    def _check_portfolio_risk(self, current_price):
        """Check if portfolio drawdown exceeds threshold."""
        try:
            portfolio_file = Path("data/portfolio.json")
            if not portfolio_file.exists():
                return False, "No portfolio data"

            with portfolio_file.open("r", encoding="utf-8") as f:
                portfolio = json.load(f)

            initial_cash = float(portfolio.get("initial_cash", self.dca.budget_usd))
            current_cash = float(portfolio.get("cash_usd", 0))
            current_btc = float(portfolio.get("btc", 0))

            current_value = current_cash + (current_btc * current_price)
            drawdown_pct = ((initial_cash - current_value) / initial_cash) * 100

            if drawdown_pct > self.max_drawdown_pct:
                return True, f"Portfolio drawdown: {drawdown_pct:.1f}% (threshold: {self.max_drawdown_pct}%)"

            return False, f"Portfolio drawdown: {drawdown_pct:.1f}% (threshold: {self.max_drawdown_pct}%)"

        except Exception as e:
            return False, f"Risk check error: {e}"

    def step(self, current_price, now=None):
        """Given current price, decide whether to DCA buy."""
        now = now or datetime.now()
        previous_price = self._last_close_price if self._last_close_price is not None else current_price
        decision = self.dca.should_buy(current_price, previous_price, now)
        self._last_close_price = current_price
        return {"action": "buy" if decision.get("should_buy") else "hold", **decision}

    def evaluate_hybrid(self, current_price, atr_value, now=None, overrides=None, swing_amount_usd=None):
        """Evaluate DCA and ATR-swing decisions together.

        `atr_value` should be *intraday* ATR when called from the live
        30-minute trading cycle (src/data/intraday_price_data.py) — see
        src/jobs/daily_regime_job.py for the separate daily-candle ATR
        used for regime classification.

        Returns a dict with keys: dca, swing_open, swing_closures,
        risk_pause, risk_message.
        """
        now = now or datetime.now()
        overrides = overrides or {}
        previous_price = self._last_close_price if self._last_close_price is not None else current_price

        risk_pause, risk_message = self._check_portfolio_risk(current_price)

        if risk_pause:
            if not self._risk_alert_sent:
                try:
                    # Fix vs. the original notebook: this imported from a
                    # stray Colab cell reference ("nGR9RlMexa0u") that isn't
                    # a real module, so the import silently failed inside
                    # the surrounding except-pass and the alert never sent.
                    from src.notify.telegram import send_message as embedded_send_message
                    embedded_send_message(f"RISK ALERT: {risk_message}. All trading paused.")
                    self._risk_alert_sent = True
                except Exception:
                    pass

            return {
                "dca": {"should_buy": False, "reason": f"Risk pause: {risk_message}"},
                "swing_open": None,
                "swing_closures": [],
                "risk_pause": True,
                "risk_message": risk_message,
            }

        if self._risk_alert_sent:
            self._risk_alert_sent = False

        dca_decision = self.dca.should_buy(current_price, previous_price, now)
        self._last_close_price = current_price

        active = self._load_active_trades()
        swing_closures = []
        for tr in list(active):
            if tr.get("status", "open") != "open":
                continue
            if current_price <= float(tr.get("stop_loss", 0.0)):
                swing_closures.append(tr)

        swing_open = None
        if self.trading_mode == "hybrid":
            enabled = bool(overrides.get("enable_swing", False))
            atr_k = float(overrides.get("atr_k_stop", 1.5))
            if enabled and atr_value and float(atr_value) > 0 and not any(t.get("status") == "open" for t in active):
                amount_usd = float(swing_amount_usd or self.dca.dca_amount_usd)
                btc_amount = amount_usd / float(current_price) if current_price > 0 else 0.0
                stop_loss = float(current_price) - atr_k * float(atr_value)
                swing_open = {
                    "trade_id": str(uuid4())[:8],
                    "entry_price": float(current_price),
                    "stop_loss": float(stop_loss),
                    "amount_usd": amount_usd,
                    "btc_amount": float(btc_amount),
                    "atr_value": float(atr_value),
                    "atr_k": float(atr_k),
                    "entry_time": now.isoformat(),
                    "status": "pending",
                    "trade_type": "swing",
                }

        return {
            "dca": dca_decision,
            "swing_open": swing_open,
            "swing_closures": swing_closures,
            "risk_pause": False,
            "risk_message": risk_message,
        }

    def record_swing_open(self, trade):
        """Persist a newly opened swing trade (mark as open)."""
        trades = self._load_active_trades()
        trade = dict(trade)
        trade["status"] = "open"
        trades.append(trade)
        self._save_active_trades(trades)

    def record_swing_close(self, trade_id):
        """Remove a closed swing trade from active storage."""
        trades = self._load_active_trades()
        remaining = [t for t in trades if t.get("trade_id") != trade_id]
        self._save_active_trades(remaining)
