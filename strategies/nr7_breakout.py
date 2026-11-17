"""NR7 (Narrowest Range of 7 Bars) Compression Breakout strategy engine.

Detects short-term range compression where the prior bar's high-low range is the
narrowest of the trailing `nr7_lookback` bars, and enters LONG on an upward breakout
above the prior bar's high (with EMA trend confirmation and ATR trailing stop).
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd

from core.data_loader import check_data_quality, filter_closed_bars
from core.signals import (
    SignalDirection,
    SignalStatus,
    TradingSignal,
    create_no_trade_signal,
)

MIN_BARS_REQUIRED = 60


def compute_nr7_indicators(
    df: pd.DataFrame,
    nr7_lookback: int = 7,
    atr_period: int = 14,
    ema_period: int = 20,
) -> pd.DataFrame:
    """Compute causal NR7 compression flag, ATR, EMA20, and breakout trigger."""
    out = df.copy()
    c = out["close"].astype(float)
    h = out["high"].astype(float)
    l = out["low"].astype(float)

    tr = pd.concat([(h - l), (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(atr_period).mean()
    out["ema_fast"] = c.ewm(span=ema_period, adjust=False).mean()

    bar_range = h - l
    is_nr7_prev = (bar_range == bar_range.rolling(nr7_lookback).min()).shift(1).fillna(False)
    out["nr7_prev"] = is_nr7_prev
    out["nr7_breakout"] = is_nr7_prev & (c > h.shift(1)) & (c > out["ema_fast"])
    return out


class Nr7BreakoutStrategy:
    """NR7 Compression Breakout Strategy."""

    STRATEGY_ID = "nr7_breakout"

    def __init__(self, config: Dict[str, Any]):
        self.nr7_lookback = int(config.get("nr7_lookback", 7))
        self.atr_period = int(config.get("atr_period", 14))
        self.atr_multiplier = float(config.get("nr7_atr_stop_mult", 2.0))
        self.take_profit_atr_mult = float(config.get("nr7_tp_atr_mult", 2.5))
        self.fee_pct = float(config.get("maker_fee_pct", config.get("fee_pct", 0.0002)))
        self.slippage_pct = float(config.get("slippage_pct", 0.0001))
        self.max_stale_minutes = int(config.get("max_stale_minutes", 180))

    def evaluate(
        self,
        symbol: str,
        df: pd.DataFrame,
        existing_position: Optional[Dict[str, Any]] = None,
    ) -> TradingSignal:
        """Evaluate closed bars for an NR7 breakout entry or trailing-stop exit."""
        raw_px = float(df["close"].iloc[-1]) if (df is not None and not df.empty) else 0.0
        if df is None or len(df) < MIN_BARS_REQUIRED:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, "Insufficient data for NR7")

        closed_df = filter_closed_bars(df)
        ok, reason = check_data_quality(closed_df, max_stale_minutes=self.max_stale_minutes, min_bars=MIN_BARS_REQUIRED)
        if not ok:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, reason)

        ind = compute_nr7_indicators(
            closed_df,
            nr7_lookback=self.nr7_lookback,
            atr_period=self.atr_period,
        )
        last = ind.iloc[-1]
        price = float(last["close"])
        atr = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
        if atr <= 0 or price <= 0:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, price, "Invalid ATR/price for NR7")

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
                    exit_reason=f"NR7 Breakout trailing stop hit (${stop:.2f})",
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

        if bool(last.get("nr7_breakout", False)):
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
                signal_strength=0.78,
                suggested_stop=stop_loss,
                suggested_take_profit=take_profit,
                estimated_fee_pct=self.fee_pct,
                estimated_slippage_pct=self.slippage_pct,
                indicators={
                    "ema_fast": float(last["ema_fast"]),
                    "nr7_lookback": self.nr7_lookback,
                    "atr": atr,
                },
            )

        return create_no_trade_signal(self.STRATEGY_ID, symbol, price, "No NR7 breakout")
