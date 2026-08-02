import pandas as pd
from datetime import datetime, timedelta


class DCAStrategy:
    """
    Simple Dollar-Cost Averaging strategy for Bitcoin.
    Buys when price drops by a specified percentage.
    """

    def __init__(self, budget_usd, dca_amount_usd, dca_drop_percent, min_interval_hours=24):
        """
        Initialize DCA strategy.

        Args:
            budget_usd (float): Total budget available
            dca_amount_usd (float): Amount to buy each time
            dca_drop_percent (float): Price drop percentage to trigger buy
            min_interval_hours (int): Minimum hours between buys
        """
        self.budget_usd = budget_usd
        self.dca_amount_usd = dca_amount_usd
        self.dca_drop_percent = dca_drop_percent
        self.min_interval_hours = min_interval_hours

        self.purchases = []
        self.last_purchase_time = None
        self.total_spent = 0.0
        self.total_btc = 0.0

    def should_buy(self, current_price, previous_price, current_time):
        """
        Check if we should buy based on DCA rules.

        Returns:
            dict: Buy decision and details
        """
        if previous_price <= 0:
            return {"should_buy": False, "reason": "No previous price data"}

        price_drop = ((previous_price - current_price) / previous_price) * 100

        time_since_last = None
        if self.last_purchase_time:
            time_since_last = (current_time - self.last_purchase_time).total_seconds() / 3600

        if self.total_spent + self.dca_amount_usd > self.budget_usd:
            return {"should_buy": False, "reason": "Insufficient budget"}

        if time_since_last and time_since_last < self.min_interval_hours:
            return {
                "should_buy": False,
                "reason": f"Too soon since last purchase ({time_since_last:.1f}h < {self.min_interval_hours}h)",
            }

        if price_drop >= self.dca_drop_percent:
            return {
                "should_buy": True,
                "reason": f"Price dropped {price_drop:.2f}% (threshold: {self.dca_drop_percent}%)",
                "price_drop": price_drop,
                "amount_usd": self.dca_amount_usd,
            }

        return {"should_buy": False, "reason": f"Price drop {price_drop:.2f}% below threshold {self.dca_drop_percent}%"}

    def execute_buy(self, price_usd, amount_usd, timestamp):
        """Execute a buy order and record it."""
        btc_amount = amount_usd / price_usd

        purchase = {
            "timestamp": timestamp,
            "price_usd": price_usd,
            "amount_usd": amount_usd,
            "btc_amount": btc_amount,
            "type": "DCA",
        }

        self.purchases.append(purchase)
        self.last_purchase_time = timestamp
        self.total_spent += amount_usd
        self.total_btc += btc_amount

    def get_portfolio_summary(self, current_price):
        """Get current portfolio summary."""
        if self.total_btc == 0 or self.total_spent == 0:
            return {
                "total_btc": 0,
                "total_spent": 0,
                "current_value": 0,
                "unrealized_pnl": 0,
                "unrealized_pnl_percent": 0,
            }

        current_value = self.total_btc * current_price
        unrealized_pnl = current_value - self.total_spent
        unrealized_pnl_percent = (unrealized_pnl / self.total_spent) * 100
        average_price = self.total_spent / self.total_btc

        return {
            "total_btc": self.total_btc,
            "total_spent": self.total_spent,
            "current_value": current_value,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_percent": unrealized_pnl_percent,
            "average_price": average_price,
        }

    def get_purchase_history(self):
        """Return a copy of all recorded purchase transactions."""
        return list(self.purchases)
