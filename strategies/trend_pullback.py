"""Trend Pullback continuation strategy engine.

Buys healthy short-term pullbacks (low near EMA20 with RSI <= 44) inside confirmed
macro uptrends (close > EMA200 and EMA50 > EMA200) upon bullish confirmation candle.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from core.data_loader import check_data_quality, filter_closed_bars
from core.signals import (
    SignalDirection,
    SignalStatus,
    TradingSignal,
    create_no_trade_signal,
)

MIN_BARS_REQUIRED = 60


def compute_pullback_indicators(
    df: pd.DataFrame,
    ema_fast: int = 20,
    ema_mid: int = 50,
    ema_macro: int = 200,
    rsi_period: int = 14,
    rsi_pullback_max: float = 44.0,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Compute causal EMA ribbon, RSI pullback zone, ATR, and bounce confirmation."""
    out = df.copy()
    c = out["close"].astype(float)
    o = out["open"].astype(float)
    h = out["high"].astype(float)
    l = out["low"].astype(float)

    out["ema20"] = c.ewm(span=ema_fast, adjust=False).mean()
    out["ema50"] = c.ewm(span=ema_mid, adjust=False).mean()
    out["ema200"] = c.ewm(span=ema_macro, adjust=False).mean()

    delta = c.diff()
    gain = delta.clip(lower=0.0).rolling(rsi_period).mean()
    loss = (-delta.clip(upper=0.0)).rolling(rsi_period).mean()
    out["rsi"] = 100.0 - (100.0 / (1.0 + gain / loss.replace(0.0, np.nan)))

    tr = pd.concat([(h - l), (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(atr_period).mean()

    uptrend = (c > out["ema200"]) & (out["ema50"] > out["ema200"])
    dip = (l <= out["ema20"] * 1.005) & (out["rsi"] <= rsi_pullback_max)
    bounce = (c > o) & (c > c.shift(1))
    out["pullback_entry"] = uptrend & dip.shift(1).fillna(False) & bounce
    return out


class TrendPullbackStrategy:
    """Confirmed-Uptrend EMA Pullback Continuation Strategy."""

    STRATEGY_ID = "trend_pullback"

    def __init__(self, config: Dict[str, Any]):
        self.rsi_pullback_max = float(config.get("pullback_rsi_max", 44.0))
        self.atr_period = int(config.get("atr_period", 14))
        self.atr_multiplier = float(config.get("pullback_atr_stop_mult", 2.0))
        self.take_profit_atr_mult = float(config.get("pullback_tp_atr_mult", 2.5))
        self.fee_pct = float(config.get("maker_fee_pct", config.get("fee_pct", 0.0002)))
        self.slippage_pct = float(config.get("slippage_pct", 0.0001))
        self.max_stale_minutes = int(config.get("max_stale_minutes", 180))

    def evaluate(
        self,
        symbol: str,
        df: pd.DataFrame,
        existing_position: Optional[Dict[str, Any]] = None,
    ) -> TradingSignal:
        """Evaluate closed bars for an uptrend pullback bounce entry or trailing exit."""
        raw_px = float(df["close"].iloc[-1]) if (df is not None and not df.empty) else 0.0
        if df is None or len(df) < MIN_BARS_REQUIRED:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, "Insufficient data for pullback")

        closed_df = filter_closed_bars(df)
        ok, reason = check_data_quality(closed_df, max_stale_minutes=self.max_stale_minutes, min_bars=MIN_BARS_REQUIRED)
        if not ok:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, reason)

        ind = compute_pullback_indicators(
            closed_df,
            rsi_pullback_max=self.rsi_pullback_max,
            atr_period=self.atr_period,
        )
        last = ind.iloc[-1]
        price = float(last["close"])
        atr = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
        if atr <= 0 or price <= 0:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, price, "Invalid ATR/price for pullback")

        if existing_position is not None:
            stop = float(existing_position.get("trailing_stop", 0.0))
            if float(last["low"]) <= stop:
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=datetime.now(timezone.utc),
                    direction=SignalDirection.FLAT,
                    status=SignalStatus.EXIT,
                    reference_price=price,
                    atr=atr,
                    suggested_stop=stop,
                    exit_reason=f"Trend Pullback trailing stop hit (${stop:.2f})",
                )
            new_stop = max(stop, price - self.atr_multiplier * atr)
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                direction=SignalDirection.LONG,
                status=SignalStatus.HOLD,
                reference_price=price,
                atr=atr,
                suggested_stop=new_stop,
            )

        if bool(last.get("pullback_entry", False)):
            stop_loss = max(0.0, price - self.atr_multiplier * atr)
            take_profit = price + self.take_profit_atr_mult * atr
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                direction=SignalDirection.LONG,
                status=SignalStatus.ENTRY,
                reference_price=price,
                atr=atr,
                signal_strength=0.80,
                suggested_stop=stop_loss,
                suggested_take_profit=take_profit,
                estimated_fee_pct=self.fee_pct,
                estimated_slippage_pct=self.slippage_pct,
                indicators={
                    "ema20": float(last["ema20"]),
                    "ema200": float(last["ema200"]),
                    "rsi": float(last["rsi"]) if pd.notna(last["rsi"]) else 0.0,
                    "atr": atr,
                },
            )

        return create_no_trade_signal(self.STRATEGY_ID, symbol, price, "No trend pullback setup")
