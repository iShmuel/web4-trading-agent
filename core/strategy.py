"""Strategy module implementing technical indicators for Momentum and Mean Reversion strategies.

Provides deterministic indicator calculations:
- Donchian Breakout Channels
- Exponential Moving Average (EMA)
- Average True Range (ATR)
- Bollinger Bands (SMA, Standard Deviation, Bands, Width, Z-Score)
- Relative Strength Index (RSI)
- Average Directional Index (ADX)
"""

from typing import Tuple, Dict, Any, Optional
import numpy as np
import pandas as pd


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range (ATR)."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    return atr


def calculate_ema(series: pd.Series, period: int = 200) -> pd.Series:
    """Calculate Exponential Moving Average (EMA)."""
    return series.ewm(span=period, adjust=False).mean()


def calculate_bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    """Calculate Bollinger Bands (SMA, Upper, Lower, Width, Z-Score).

    Returns:
        (sma, upper, lower, width, z_score)
    """
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    width = (upper - lower) / sma.replace(0, np.nan)
    z_score = (series - sma) / std.replace(0, np.nan)
    return sma, upper, lower, width, z_score


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index (RSI) using exponential weighting."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1.0 / period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1.0 / period, adjust=False).mean()

    # Handle division by zero edge cases
    rs = gain / loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # If loss is 0 (price only gained), RSI is 100
    rsi = rsi.where(loss != 0, 100.0)
    # If gain is 0 (price only lost), RSI is 0
    rsi = rsi.where(gain != 0, 0.0)
    # If both zero (flat line), RSI is 50
    rsi = rsi.where((gain != 0) | (loss != 0), 50.0)
    return rsi


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average Directional Index (ADX) to gauge trend strength."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr_adx = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100.0 * (plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_adx.replace(0, np.nan))
    minus_di = 100.0 * (minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_adx.replace(0, np.nan))

    dx = 100.0 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.ewm(alpha=1.0 / period, adjust=False).mean()
    return adx


def compute_indicators(
    df: pd.DataFrame,
    breakout_lookback: int = 24,
    ema_trend_filter: int = 200,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Compute momentum/breakout strategy indicators and add them as columns."""
    df = df.copy()

    # Donchian Breakout High: highest high of previous N bars (excluding current candle)
    df["breakout_high"] = df["high"].shift(1).rolling(window=breakout_lookback).max()

    # Donchian Breakdown Low: lowest low of previous N bars (excluding current candle)
    df["breakdown_low"] = df["low"].shift(1).rolling(window=breakout_lookback).min()

    # Macro Trend Filter: EMA
    df["ema_filter"] = calculate_ema(df["close"], period=ema_trend_filter)

    # Volatility: ATR
    df["atr"] = calculate_atr(df, period=atr_period)

    # Trend Conditions
    df["in_uptrend"] = df["close"] > df["ema_filter"]
    df["in_downtrend"] = df["close"] < df["ema_filter"]

    # Entry Triggers
    df["long_signal"] = df["in_uptrend"] & (df["close"] > df["breakout_high"])
    df["short_signal"] = df["in_downtrend"] & (df["close"] < df["breakdown_low"])

    # Maintain entry_signal for backwards compatibility with long-only calls
    df["entry_signal"] = df["long_signal"]

    return df


def compute_mean_reversion_indicators(
    df: pd.DataFrame,
    bb_period: int = 20,
    bb_std: float = 2.0,
    rsi_period: int = 14,
    atr_period: int = 14,
    adx_period: int = 14,
) -> pd.DataFrame:
    """Compute Bollinger Bands, RSI, ATR, and ADX for mean reversion analysis."""
    df = df.copy()

    sma, upper, lower, width, z_score = calculate_bollinger_bands(
        df["close"], period=bb_period, std_dev=bb_std
    )
    df["bb_sma"] = sma
    df["bb_std"] = df["close"].rolling(window=bb_period).std()
    df["bb_upper"] = upper
    df["bb_lower"] = lower
    df["bb_width"] = width
    df["z_score"] = z_score

    df["rsi"] = calculate_rsi(df["close"], period=rsi_period)
    df["atr"] = calculate_atr(df, period=atr_period)
    df["adx"] = calculate_adx(df, period=adx_period)

    return df
