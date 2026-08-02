"""
Daily regime job — the "slow" half of the Option C split.

Runs once a day (scheduled in scheduler.py): fetches daily candles,
computes daily ATR + the ARIMA forecast, optionally calls the LLM
regime classifier, and writes the result to data/runtime_overrides.json
via write_overrides(). The 30-minute trading cycle in src/main.py just
reads whatever this job last wrote — it does not recompute forecast or
regime itself, and it sizes stop-losses from the separate intraday ATR
path in src/data/intraday_price_data.py, not from anything in here.

This is what keeps the two cadences honest: a swing trade opened at
11pm gets a stop sized from the last few hours of real volatility, but
the "are we trending or ranging" call only updates once a day, which
is genuinely how often that question's answer changes.
"""

import logging

from src.data.price_data import fetch_bitcoin_data, calculate_atr
from src.ml.forecaster import forecast_next
from src.ml.threshold_adapter import adapt_thresholds_with_llm, write_overrides

logger = logging.getLogger("daily_regime_job")


def run(cfg: dict, lookback_days: str = "60d", atr_period: int = 14):
    """
    Execute one daily regime recompute. Safe to call multiple times a
    day if needed (e.g. manual re-run) — it always overwrites the
    overrides file with a fresh result rather than accumulating state.
    """
    df = fetch_bitcoin_data(period=lookback_days)
    if df.empty or "close" not in df.columns:
        logger.warning("Daily regime job: no daily candle data available, skipping")
        return None

    # calculate_atr()/forecast_next() expect capitalized OHLC columns.
    df = df.rename(columns={"close": "Close", "high": "High", "low": "Low"})

    daily_atr_series = calculate_atr(df, period=atr_period)
    daily_atr = float(daily_atr_series.iloc[-1]) if not daily_atr_series.empty else 0.0

    pred_return, pred_strength = forecast_next(df)
    current_price = float(df["Close"].iloc[-1])

    overrides = adapt_thresholds_with_llm(
        base_cfg=cfg,
        pred_return=pred_return,
        pred_strength=pred_strength,
        atr_value=daily_atr,
        current_price=current_price,
        # Extra context for the LLM call beyond pred_return/pred_strength.
        feature_snapshot={"daily_atr_pct_of_price": round(daily_atr / current_price, 5) if current_price else 0.0},
    )

    write_overrides(overrides)
    logger.info(
        "Daily regime job complete: pred_return=%.5f pred_strength=%.3f daily_atr=%.2f -> %s",
        pred_return,
        pred_strength,
        daily_atr,
        overrides,
    )
    return overrides
