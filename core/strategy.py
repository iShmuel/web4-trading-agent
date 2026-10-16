"""Strategy module implementing Donchian Breakout, 200 EMA trend filter, and ATR."""

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


def compute_indicators(
    df: pd.DataFrame,
    breakout_lookback: int = 24,
    ema_trend_filter: int = 200,
    atr_period: int = 14,
) -> pd.DataFrame:
    """
    Compute all strategy indicators and add them as columns to the dataframe.
    """
    df = df.copy()

    # Donchian Breakout Line: highest high of previous N bars (excluding current candle)
    df["breakout_high"] = df["high"].shift(1).rolling(window=breakout_lookback).max()

    # Macro Trend Filter: 200 EMA
    df["ema_filter"] = calculate_ema(df["close"], period=ema_trend_filter)

    # Volatility: ATR
    df["atr"] = calculate_atr(df, period=atr_period)

    # Long Trend Condition: price above 200 EMA
    df["in_uptrend"] = df["close"] > df["ema_filter"]

    # Entry Trigger: price breaks out above previous N-period high while in uptrend
    df["entry_signal"] = df["in_uptrend"] & (df["close"] > df["breakout_high"])

    return df
