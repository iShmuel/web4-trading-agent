"""Data loader module for fetching and caching historical OHLCV price feeds."""

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import ccxt

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def get_data_dir() -> Path:
    """Ensure data directory exists and return Path."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def fetch_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    days: int = 180,
    exchange_name: str = "binance",
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    Fetch historical OHLCV data with local caching.
    Uses public exchange APIs via CCXT (no API key required).
    """
    clean_symbol = symbol.replace("/", "_")
    cache_path = get_data_dir() / f"{exchange_name}_{clean_symbol}_{timeframe}_{days}d.csv"

    if cache_path.exists() and not force_refresh:
        df = pd.read_csv(cache_path, parse_dates=["timestamp"])
        df.set_index("timestamp", inplace=True)
        print(f"[DataLoader] Loaded {len(df)} candles from local cache: {cache_path.name}")
        return df

    print(f"[DataLoader] Fetching ~{days} days of {timeframe} candles for {symbol} from {exchange_name}...")
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

            # Break if reached current time
            if candles[-1][0] >= exchange.milliseconds() - ms_per_candle:
                break

            time.sleep(exchange.rateLimit / 1000.0)
        except Exception as e:
            print(f"[DataLoader] Warning during fetch: {e}")
            break

    if not all_candles:
        raise RuntimeError(f"Failed to fetch OHLCV data for {symbol} from {exchange_name}")

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.drop_duplicates(subset=["timestamp"], keep="last", inplace=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)

    # Save to local CSV cache
    df.to_csv(cache_path)
    print(f"[DataLoader] Saved {len(df)} candles to local cache: {cache_path.name}")
    return df


if __name__ == "__main__":
    df = fetch_ohlcv("BTC/USDT", "1h", days=90)
    print(f"Data preview:\n{df.tail()}")
