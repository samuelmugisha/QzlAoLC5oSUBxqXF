import pandas as pd
import requests
from datetime import datetime, timedelta


def fetch_bitcoin_data(period="30d"):
    """
    Fetches historical Bitcoin (BTC-USD) daily data from Coinbase Exchange API.

    Args:
        period (str): The period for which to fetch data (e.g., "30d", "60d").
                      Currently supports "Xd" where X is number of days.

    Returns:
        pd.DataFrame: DataFrame with historical price data, or empty DataFrame if failed.
                      Columns: 'start', 'low', 'high', 'open', 'close', 'volume'.
                      'start' column is datetime.
    """
    product_id = 'BTC-USD'
    granularity = 'ONE_DAY'  # Daily candles — see intraday_price_data.py for the
                             # 30-minute path used to size swing stop-losses.

    try:
        days = int(period.replace('d', ''))
    except ValueError:
        print(f"Invalid period format: {period}. Using default 30 days.")
        days = 30

    end_time = datetime.now()
    start_time = end_time - timedelta(days=days)

    start_unix = str(int(start_time.timestamp()))
    end_unix = str(int(end_time.timestamp()))

    historical_url = f"https://api.coinbase.com/api/v3/brokerage/market/products/{product_id}/candles"

    params = {
        'start': start_unix,
        'end': end_unix,
        'granularity': granularity
    }

    try:
        response_historical = requests.get(historical_url, params=params, timeout=10)
        response_historical.raise_for_status()
        historical_data = response_historical.json()

        if 'candles' in historical_data:
            df_historical = pd.DataFrame(historical_data['candles'])
            df_historical['start'] = pd.to_datetime(df_historical['start'].astype(int), unit='s')

            numeric_cols = ['low', 'high', 'open', 'close', 'volume']
            for col in numeric_cols:
                df_historical[col] = pd.to_numeric(df_historical[col])

            return df_historical
        else:
            print(f"Could not retrieve historical Bitcoin data, 'candles' key not found. "
                  f"Error: {historical_data.get('error', 'Unknown error.')}")
            return pd.DataFrame()
    except requests.exceptions.RequestException as e:
        print(f"Failed to retrieve historical Bitcoin data. Request Error: {e}")
        return pd.DataFrame()
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return pd.DataFrame()


def calculate_atr(data, period=14):
    """Calculate Average True Range (ATR) indicator.

    Expects columns 'High', 'Low', 'Close' (capitalized) — callers
    fetching from Coinbase's lowercase 'high'/'low'/'close' should
    rename before calling this.
    """
    if data.empty or len(data) < period:
        print(f"Need at least {period} data points for ATR")
        return pd.Series(dtype=float)

    try:
        high_low = data['High'] - data['Low']
        high_close = abs(data['High'] - data['Close'].shift())
        low_close = abs(data['Low'] - data['Close'].shift())

        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = true_range.rolling(window=period).mean()

        return atr
    except Exception as e:
        print(f"ATR calculation failed: {e}")
        return pd.Series(dtype=float)


def get_latest_price_and_atr(period="30d", atr_period=14):
    """Fetches the current Bitcoin price and calculates the latest *daily* ATR.

    Used for the daily regime job (src/jobs/daily_regime_job.py) — NOT for
    sizing swing stop-losses on the 30-minute trading cycle. For that,
    use get_latest_price_and_intraday_atr() in src/data/intraday_price_data.py.
    """
    try:
        url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        spot_data = response.json()
        current_price = float(spot_data['data']['amount'])

        df_historical = fetch_bitcoin_data(period=period)
        if df_historical.empty:
            return {"error": "Could not fetch historical data for ATR calculation"}

        df_historical = df_historical.rename(columns={'close': 'Close', 'high': 'High', 'low': 'Low'})

        atr_series = calculate_atr(df_historical, period=atr_period)
        latest_atr = float(atr_series.iloc[-1]) if not atr_series.empty else 0.0

        return {"price": current_price, "atr": latest_atr}

    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {e}"}
    except Exception as e:
        return {"error": f"Error getting latest price and ATR: {e}"}
