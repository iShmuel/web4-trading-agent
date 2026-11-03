"""Unit tests for data quality verification, staleness detection, and symbol classification."""

from datetime import datetime, timezone, timedelta
import pandas as pd
import pytest

from core.data_loader import (
    is_crypto_symbol,
    check_data_quality,
    filter_closed_bars,
    parse_timeframe_to_timedelta,
)


def test_parse_timeframe_to_timedelta():
    assert parse_timeframe_to_timedelta("15m") == timedelta(minutes=15)
    assert parse_timeframe_to_timedelta("1h") == timedelta(hours=1)
    assert parse_timeframe_to_timedelta("4h") == timedelta(hours=4)
    assert parse_timeframe_to_timedelta("1d") == timedelta(days=1)


def test_filter_closed_bars_drops_forming_candle():
    # 3 bars: 10:00, 11:00, 12:00
    ref_time = datetime(2026, 11, 3, 12, 30, tzinfo=timezone.utc)
    dates = pd.date_range("2026-11-03 10:00", periods=3, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0],
            "high": [105.0, 106.0, 107.0],
            "low": [99.0, 100.0, 101.0],
            "close": [101.0, 102.0, 103.0],
            "volume": [1000.0, 1200.0, 200.0],  # 12:00 bar has partial volume!
        },
        index=dates,
    )

    filtered = filter_closed_bars(df, timeframe="1h", as_of=ref_time)
    # 10:00 bar closed at 11:00 (<= 12:30) -> kept
    # 11:00 bar closed at 12:00 (<= 12:30) -> kept
    # 12:00 bar closes at 13:00 (> 12:30) -> MUST be dropped!
    assert len(filtered) == 2
    assert filtered.index[-1] == pd.Timestamp("2026-11-03 11:00", tz="UTC")


def test_filter_closed_bars_keeps_all_historical_bars():
    ref_time = datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc)
    dates = pd.date_range("2026-10-01 00:00", periods=50, freq="1h", tz="UTC")
    df = pd.DataFrame({"open": [10.0] * 50, "high": [11.0] * 50, "low": [9.0] * 50, "close": [10.5] * 50}, index=dates)

    filtered = filter_closed_bars(df, timeframe="1h", as_of=ref_time)
    assert len(filtered) == 50


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
