"""
Intraday data path — Option C.

This module exists to answer one specific question on the same clock
the trading cycle actually runs on: "how volatile has BTC been over the
last day or two, right now?" That's what should size a new swing
trade's stop-loss distance.

It is deliberately separate from src/data/price_data.py, which stays on
daily candles and continues to answer the slower question ("what
regime are we in this week?") for the forecaster / LLM regime call in
src/jobs/daily_regime_job.py. Two data-fetching paths, two different
questions — see the architecture discussion for why that split is
preferable to forcing one candle size to serve both jobs.
"""

from datetime import datetime, timedelta

import pandas as pd
import requests

PRODUCT_ID = "BTC-USD"
CANDLES_URL = f"https://api.coinbase.com/api/v3/brokerage/market/products/{PRODUCT_ID}/candles"
SPOT_URL = "https://api.coinbase.com/v2/prices/BTC-USD/spot"


def fetch_intraday_candles(granularity: str = "THIRTY_MINUTE", lookback_hours: int = 72) -> pd.DataFrame:
    """
    Fetch recent intraday candles from Coinbase.

    granularity: one of Coinbase's supported values — ONE_MINUTE,
        FIVE_MINUTE, FIFTEEN_MINUTE, THIRTY_MINUTE, ONE_HOUR, TWO_HOUR,
        SIX_HOUR. Defaults to THIRTY_MINUTE to match the default
        DATA_FETCH_INTERVAL_MIN trading cycle.
    lookback_hours: how far back to pull. 72 hours at THIRTY_MINUTE
        granularity is 144 candles — comfortably under Coinbase's
        300-candle-per-request limit while giving ATR(14) plenty of
        history to smooth over.

    Returns an empty DataFrame on any failure — callers should treat
    that the same way the existing fetch_bitcoin_data() does.
    """
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=lookback_hours)

    params = {
        "start": str(int(start_time.timestamp())),
        "end": str(int(end_time.timestamp())),
        "granularity": granularity,
    }

    try:
        response = requests.get(CANDLES_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "candles" not in data:
            return pd.DataFrame()

        df = pd.DataFrame(data["candles"])
        df["start"] = pd.to_datetime(df["start"].astype(int), unit="s")
        for col in ("low", "high", "open", "close", "volume"):
            df[col] = pd.to_numeric(df[col])
        # Coinbase returns newest-first; sort ascending for rolling calcs.
        df = df.sort_values("start").reset_index(drop=True)
        # Match the capitalization calculate_atr() expects.
        df.rename(columns={"close": "Close", "high": "High", "low": "Low"}, inplace=True)
        return df
    except requests.exceptions.RequestException:
        return pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def get_latest_price_and_intraday_atr(
    atr_period: int = 14,
    granularity: str = "THIRTY_MINUTE",
    lookback_hours: int = 72,
) -> dict:
    """
    Drop-in replacement for get_latest_price_and_atr() specifically for
    the per-cycle swing stop-loss sizing path — same {"price", "atr"}
    shape, but the ATR is computed from candles at (roughly) the
    trading cycle's own granularity instead of daily bars.

    Use this for evaluate_hybrid()'s atr_value argument. Continue using
    the existing daily-candle get_latest_price_and_atr() /
    fetch_bitcoin_data() for anything regime- or forecast-related —
    see src/jobs/daily_regime_job.py.
    """
    # Local import to avoid a hard dependency between this module and
    # price_data.py beyond the one function we actually need.
    from src.data.price_data import calculate_atr

    try:
        spot_resp = requests.get(SPOT_URL, timeout=10)
        spot_resp.raise_for_status()
        current_price = float(spot_resp.json()["data"]["amount"])

        df = fetch_intraday_candles(granularity=granularity, lookback_hours=lookback_hours)
        if df.empty:
            return {"error": "Could not fetch intraday candles for ATR calculation"}

        atr_series = calculate_atr(df, period=atr_period)
        latest_atr = float(atr_series.iloc[-1]) if not atr_series.empty else 0.0

        return {
            "price": current_price,
            "atr": latest_atr,
            "granularity": granularity,
            "candle_count": len(df),
        }
    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {e}"}
    except Exception as e:
        return {"error": f"Error getting latest price and intraday ATR: {e}"}
