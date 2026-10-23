"""Unit tests for data quality verification, staleness detection, and symbol classification."""

from datetime import datetime, timezone, timedelta
import pandas as pd
import pytest

from core.data_loader import is_crypto_symbol, check_data_quality


def test_is_crypto_symbol_classification():
    # Crypto symbols
    assert is_crypto_symbol("BTC/USDT") is True
    assert is_crypto_symbol("ETH/USDT") is True
    assert is_crypto_symbol("SOL-USD") is True
    assert is_crypto_symbol("BTCUSDT") is True

    # Stock / ETF symbols
    assert is_crypto_symbol("NVDA") is False
    assert is_crypto_symbol("SPY") is False
    assert is_crypto_symbol("AAPL") is False
    assert is_crypto_symbol("TSLA") is False


def test_check_data_quality_valid():
    dates = pd.date_range(end=datetime.now(timezone.utc), periods=50, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [100.0] * 50,
            "high": [105.0] * 50,
            "low": [95.0] * 50,
            "close": [102.0] * 50,
            "volume": [1000.0] * 50,
        },
        index=dates,
    )
    valid, reason = check_data_quality(df, max_stale_minutes=120)
    assert valid is True
    assert "passed" in reason.lower()


def test_check_data_quality_detects_anomalies():
    dates = pd.date_range(end=datetime.now(timezone.utc), periods=50, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [100.0] * 50,
            "high": [90.0] * 50,  # High is lower than Low!
            "low": [95.0] * 50,
            "close": [92.0] * 50,
            "volume": [1000.0] * 50,
        },
        index=dates,
    )
    valid, reason = check_data_quality(df)
    assert valid is False
    assert "high price is lower than low price" in reason


def test_check_data_quality_detects_stale_data():
    # Last bar is 5 hours ago
    stale_time = datetime.now(timezone.utc) - timedelta(hours=5)
    dates = pd.date_range(end=stale_time, periods=50, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [100.0] * 50,
            "high": [105.0] * 50,
            "low": [95.0] * 50,
            "close": [102.0] * 50,
            "volume": [1000.0] * 50,
        },
        index=dates,
    )
    valid, reason = check_data_quality(df, max_stale_minutes=120)  # max 2 hours
    assert valid is False
    assert "stale" in reason.lower()
