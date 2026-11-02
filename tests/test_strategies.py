"""Unit tests for independent strategy engines: Momentum and Mean Reversion."""

import numpy as np
import pandas as pd
import pytest

from core.signals import SignalDirection, SignalStatus
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy


def create_trend_ohlcv(bars: int = 100, trend: float = 1.0) -> pd.DataFrame:
    """Create OHLCV DataFrame with strong upward or downward trend."""
    dates = pd.date_range("2026-01-01", periods=bars, freq="1h", tz="UTC")
    base = 100.0 + np.arange(bars) * trend
    df = pd.DataFrame(
        {
            "open": base - 0.2,
            "high": base + 1.0,
            "low": base - 0.5,
            "close": base + 0.5,
            "volume": 1000.0,
        },
        index=dates,
    )
    df.index.name = "timestamp"
    return df


def create_range_ohlcv(bars: int = 100, amplitude: float = 5.0) -> pd.DataFrame:
    """Create range-bound oscillating OHLCV DataFrame."""
    dates = pd.date_range("2026-01-01", periods=bars, freq="1h", tz="UTC")
    sine = 100.0 + amplitude * np.sin(np.linspace(0, 6 * np.pi, bars))
    df = pd.DataFrame(
        {
            "open": sine,
            "high": sine + 1.0,
            "low": sine - 1.0,
            "close": sine,
            "volume": 1000.0,
        },
        index=dates,
    )
    df.index.name = "timestamp"
    return df


def test_momentum_strategy_long_signal():
    df = create_trend_ohlcv(bars=100, trend=1.0)
    config = {
        "breakout_lookback": 20,
        "ema_trend_filter": 30,
        "atr_period": 14,
        "atr_multiplier": 2.5,
        "allow_short": True,
        "max_stale_minutes": 0,
    }
    strategy = MomentumStrategy(config)
    sig = strategy.evaluate("BTC/USDT", df)

    assert sig.direction == SignalDirection.LONG
    assert sig.status == SignalStatus.ENTRY
    assert sig.suggested_stop < sig.reference_price


def test_momentum_strategy_exit_on_trailing_stop():
    df = create_trend_ohlcv(bars=100, trend=1.0)
    config = {"breakout_lookback": 20, "max_stale_minutes": 0}
    strategy = MomentumStrategy(config)

    # Position holding with stop above current price -> should trigger exit
    cur_price = float(df.iloc[-1]["close"])
    pos = {
        "symbol": "BTC/USDT",
        "entry_price": cur_price - 10.0,
        "trailing_stop": cur_price + 5.0,  # stop breached
        "side": "LONG",
    }
    sig = strategy.evaluate("BTC/USDT", df, existing_position=pos)
    assert sig.status == SignalStatus.EXIT
    assert "Trailing Stop Hit" in sig.exit_reason


def test_mean_reversion_strategy_oversold_signal():
    # Construct data where last bar drops sharply below lower BB in a range
    df = create_range_ohlcv(bars=100, amplitude=4.0)
    # Force last bar to gap down sharply to trigger oversold
    df.iloc[-1, df.columns.get_loc("close")] = 85.0
    df.iloc[-1, df.columns.get_loc("low")] = 84.0

    config = {
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_period": 14,
        "rsi_oversold": 35,
        "adx_threshold": 45,
        "max_stale_minutes": 0,
    }
    strategy = MeanReversionStrategy(config)
    sig = strategy.evaluate("SPY", df)

    assert sig.direction == SignalDirection.LONG
    assert sig.status == SignalStatus.ENTRY
    assert sig.suggested_take_profit > sig.reference_price  # Target is BB SMA


def test_mean_reversion_strategy_suppression_on_strong_trend():
    # In a strong trend, ADX is high -> Mean reversion must decline to trade
    df = create_trend_ohlcv(bars=100, trend=3.0)
    config = {
        "adx_threshold": 20,  # strict trend filter
        "max_stale_minutes": 0,
    }
    strategy = MeanReversionStrategy(config)
    sig = strategy.evaluate("SPY", df)

    # Must be NO_TRADE due to trend filter
    assert sig.status == SignalStatus.NO_TRADE
    assert "trend" in sig.no_trade_reason.lower() or "no mean reversion" in sig.no_trade_reason.lower()


def test_mean_reversion_halfway_scale_out_take_profit():
    # Construct oscillating df where entry was at 90, SMA is at 100, and current price reaches 96 (over halfway)
    df = create_range_ohlcv(bars=100, amplitude=5.0)
    config = {"mr_scale_out_enabled": True, "mr_scale_out_ratio": 0.5, "max_stale_minutes": 0}
    strategy = MeanReversionStrategy(config)

    pos = {
        "symbol": "SOL/USDT",
        "entry_price": 90.0,
        "trailing_stop": 85.0,
        "side": "LONG",
        "tp_hit": False,
    }
    # Current close is 100, well above halfway target (95)
    sig = strategy.evaluate("SOL/USDT", df, existing_position=pos)
    assert sig.status == SignalStatus.PARTIAL_EXIT
    assert sig.take_profit_ratio == 0.5
    assert "Halfway Scale-Out" in sig.exit_reason


def test_mean_reversion_bullish_liquidity_sweep_reclaim():
    """Verify a stop-hunt sweep below the prior 20-bar low followed by a green reclaim triggers LONG."""
    df = create_range_ohlcv(bars=80, amplitude=2.0)
    # Create a controlled pullback so RSI is < 40 (around ~34, above the strict 30 threshold)
    for idx in range(-6, -1):
        df.iloc[idx, df.columns.get_loc("close")] = 98.5
        df.iloc[idx, df.columns.get_loc("open")] = 99.0
        df.iloc[idx, df.columns.get_loc("low")] = 98.0
        df.iloc[idx, df.columns.get_loc("high")] = 99.2

    prior_low_20 = float(df["low"].iloc[-21:-1].min())
    # Latest candle sweeps below prior_low_20, then reclaims above prior_low_20 and closes green (> open)
    df.iloc[-1, df.columns.get_loc("open")] = prior_low_20 - 0.1
    df.iloc[-1, df.columns.get_loc("low")] = prior_low_20 - 1.5
    df.iloc[-1, df.columns.get_loc("close")] = prior_low_20 + 0.3
    df.iloc[-1, df.columns.get_loc("high")] = prior_low_20 + 0.5

    strategy = MeanReversionStrategy(
        {
            "enable_liquidity_sweep": True,
            "sweep_lookback": 20,
            "sweep_rsi_max": 45.0,
            "adx_threshold": 50,
            "max_stale_minutes": 0,
        }
    )
    sig = strategy.evaluate("SOL/USDT", df, existing_position=None)
    assert sig.status == SignalStatus.ENTRY
    assert sig.direction == SignalDirection.LONG
    assert sig.indicators.get("liquidity_sweep") == "BULLISH_SWEEP_RECLAIM"

