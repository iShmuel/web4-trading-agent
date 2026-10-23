"""Data loader module for fetching and caching historical OHLCV price feeds.
Supports both Crypto (via CCXT/Binance) and US Stocks/ETFs (via yfinance).
Provides data validation, staleness checks, and quality assurance.
"""

import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
import ccxt
import yfinance as yf

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def get_data_dir() -> Path:
    """Ensure data directory exists and return Path."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def is_crypto_symbol(symbol: str) -> bool:
    """Check if the symbol is a crypto trading pair."""
    sym = symbol.strip().upper()
    return ("/" in sym) or sym.endswith("USDT") or ("-" in sym and sym.endswith("USD"))


def check_data_quality(
    df: Optional[pd.DataFrame],
    max_stale_minutes: int = 120,
    min_bars: int = 30,
) -> Tuple[bool, str]:
    """Validate DataFrame integrity, freshness, and absence of anomalies.

    Returns:
        (is_valid, reason)
    """
    if df is None or df.empty:
        return False, "Dataframe is empty or None"

    if len(df) < min_bars:
        return False, f"Insufficient data: {len(df)} bars < {min_bars} minimum"

    required_cols = ["open", "high", "low", "close"]
    for col in required_cols:
        if col not in df.columns:
            return False, f"Missing required column: {col}"
        if df[col].isna().any():
            return False, f"Column '{col}' contains NaN values"
        if (df[col] <= 0).any():
            return False, f"Column '{col}' contains non-positive values"

    # Check high >= low invariant
    if (df["high"] < df["low"]).any():
        return False, "Data anomaly: high price is lower than low price"

    # Check chronological index
    if not df.index.is_monotonic_increasing:
        return False, "Timestamps are not strictly monotonically increasing"

    # Staleness check
    last_ts = df.index[-1]
    if hasattr(last_ts, "tzinfo") and last_ts.tzinfo is not None:
        now_utc = datetime.now(timezone.utc)
        age_minutes = (now_utc - last_ts).total_seconds() / 60.0
        if max_stale_minutes > 0 and age_minutes > max_stale_minutes:
            return False, f"Data is stale: last bar was {age_minutes:.1f} minutes ago (limit: {max_stale_minutes}m)"

    return True, "Data quality passed"


def fetch_crypto_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    days: int = 180,
    exchange_name: str = "binance",
) -> pd.DataFrame:
    """Fetch crypto candles from exchange via CCXT."""
    print(f"[DataLoader] Fetching ~{days} days of {timeframe} candles for crypto {symbol} from {exchange_name}...")
    exchange_class = getattr(ccxt, exchange_name)
    exchange = exchange_class({"enableRateLimit": True, "timeout": 30000})

    ms_per_candle = exchange.parse_timeframe(timeframe) * 1000
    total_candles_needed = int((days * 24 * 60 * 60 * 1000) / ms_per_candle)
    limit_per_batch = 1000

    since = exchange.milliseconds() - (total_candles_needed * ms_per_candle)
    all_candles = []

    while True:
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit_per_batch)
            if not candles:
                break

            all_candles.extend(candles)
            since = candles[-1][0] + ms_per_candle

            if candles[-1][0] >= exchange.milliseconds() - ms_per_candle:
                break

            time.sleep(exchange.rateLimit / 1000.0)
        except Exception as e:
            print(f"[DataLoader] Warning during crypto fetch: {e}")
            break

    if not all_candles:
        raise RuntimeError(f"Failed to fetch OHLCV data for {symbol} from {exchange_name}")

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.drop_duplicates(subset=["timestamp"], keep="last", inplace=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df


def fetch_stock_ohlcv(
    symbol: str = "NVDA",
    timeframe: str = "1h",
    days: int = 180,
) -> pd.DataFrame:
    """Fetch stock/ETF candles from Yahoo Finance."""
    print(f"[DataLoader] Fetching ~{days} days of {timeframe} candles for stock {symbol} from Yahoo Finance...")

    # Map timeframe string to yfinance interval
    interval_map = {"1h": "60m", "1d": "1d", "15m": "15m", "4h": "1h"}
    interval = interval_map.get(timeframe, "60m")

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)

    ticker = yf.Ticker(symbol)
    raw_df = ticker.history(start=start_date.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"), interval=interval)

    if raw_df.empty:
        raise RuntimeError(f"Failed to fetch stock data for {symbol} from Yahoo Finance.")

    df = pd.DataFrame()
    df["open"] = raw_df["Open"].astype(float)
    df["high"] = raw_df["High"].astype(float)
    df["low"] = raw_df["Low"].astype(float)
    df["close"] = raw_df["Close"].astype(float)
    df["volume"] = raw_df["Volume"].astype(float)
    df.index.name = "timestamp"
    df.index = pd.to_datetime(df.index, utc=True)
    df.sort_index(inplace=True)
    return df


def fetch_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    days: int = 180,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Unified entry point for both Crypto and Stocks/ETFs with local CSV caching.
    """
    clean_symbol = symbol.replace("/", "_")
    source_prefix = "crypto" if is_crypto_symbol(symbol) else "stock"
    cache_path = get_data_dir() / f"{source_prefix}_{clean_symbol}_{timeframe}_{days}d.csv"

    if cache_path.exists() and not force_refresh:
        df = pd.read_csv(cache_path, parse_dates=["timestamp"])
        df.set_index("timestamp", inplace=True)
        print(f"[DataLoader] Loaded {len(df)} candles from local cache: {cache_path.name}")
        return df

    if is_crypto_symbol(symbol):
        df = fetch_crypto_ohlcv(symbol=symbol, timeframe=timeframe, days=days)
    else:
        df = fetch_stock_ohlcv(symbol=symbol, timeframe=timeframe, days=days)

    # Save to local CSV cache
    df.to_csv(cache_path)
    print(f"[DataLoader] Saved {len(df)} candles to local cache: {cache_path.name}")
    return df


if __name__ == "__main__":
    # Test stock fetch
    stock_df = fetch_ohlcv("NVDA", "1h", days=60)
    print("Stock preview:\n", stock_df.tail())
