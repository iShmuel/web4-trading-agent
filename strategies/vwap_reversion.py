"""VWAP Fair-Value Reversion strategy engine.

Anchors fair value to 24-bar Rolling VWAP (Volume-Weighted Average Price) and enters
LONG when price stretches below VWAP - 1.8*std with RSI <= 38 upon a bullish bounce.
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


def compute_vwap_indicators(
    df: pd.DataFrame,
    vwap_window: int = 24,
    std_mult: float = 1.8,
    rsi_period: int = 14,
    rsi_max: float = 38.0,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Compute causal Rolling VWAP, distance std, RSI, ATR, and bounce entry."""
    out = df.copy()
    c = out["close"].astype(float)
    o = out["open"].astype(float)
    h = out["high"].astype(float)
    l = out["low"].astype(float)
    v = out["volume"].astype(float) if "volume" in out.columns else pd.Series(1.0, index=out.index)

    tp = (h + l + c) / 3.0
    pv = (tp * v).rolling(vwap_window).sum()
    vs = v.rolling(vwap_window).sum().replace(0.0, np.nan)
    out["vwap"] = pv / vs
    out["vwap_std"] = (c - out["vwap"]).rolling(vwap_window).std().replace(0.0, np.nan)

    tr = pd.concat([(h - l), (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(atr_period).mean()

    delta = c.diff()
    gain = delta.clip(lower=0.0).rolling(rsi_period).mean()
    loss = (-delta.clip(upper=0.0)).rolling(rsi_period).mean()
    out["rsi"] = 100.0 - (100.0 / (1.0 + gain / loss.replace(0.0, np.nan)))

    stretched = (c < (out["vwap"] - std_mult * out["vwap_std"])).shift(1).fillna(False)
    oversold = (out["rsi"].shift(1) <= rsi_max).fillna(False)
    bounce = c > o
    out["vwap_entry"] = stretched & oversold & bounce
    return out


class VwapReversionStrategy:
    """Rolling VWAP Fair-Value Reversion Strategy."""

    STRATEGY_ID = "vwap_reversion"

    def __init__(self, config: Dict[str, Any]):
        self.vwap_window = int(config.get("vwap_window", 24))
        self.std_mult = float(config.get("vwap_std_mult", 1.8))
        self.rsi_max = float(config.get("vwap_rsi_max", 38.0))
        self.atr_period = int(config.get("atr_period", 14))
        self.atr_multiplier = float(config.get("vwap_atr_stop_mult", 2.0))
        self.take_profit_atr_mult = float(config.get("vwap_tp_atr_mult", 2.5))
        self.fee_pct = float(config.get("maker_fee_pct", config.get("fee_pct", 0.0002)))
        self.slippage_pct = float(config.get("slippage_pct", 0.0001))
        self.max_stale_minutes = int(config.get("max_stale_minutes", 180))

    def evaluate(
        self,
        symbol: str,
        df: pd.DataFrame,
        existing_position: Optional[Dict[str, Any]] = None,
    ) -> TradingSignal:
        """Evaluate closed bars for a VWAP fair-value reversion entry or trailing exit."""
        raw_px = float(df["close"].iloc[-1]) if (df is not None and not df.empty) else 0.0
        if df is None or len(df) < MIN_BARS_REQUIRED:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, "Insufficient data for VWAP")

        closed_df = filter_closed_bars(df)
        ok, reason = check_data_quality(closed_df, max_stale_minutes=self.max_stale_minutes, min_bars=MIN_BARS_REQUIRED)
        if not ok:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, reason)

        ind = compute_vwap_indicators(
            closed_df,
            vwap_window=self.vwap_window,
            std_mult=self.std_mult,
            rsi_max=self.rsi_max,
            atr_period=self.atr_period,
        )
        last = ind.iloc[-1]
        price = float(last["close"])
        atr = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
        if atr <= 0 or price <= 0:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, price, "Invalid ATR/price for VWAP")

        if existing_position is not None:
            stop = float(existing_position.get("trailing_stop", 0.0))
            ep = float(existing_position.get("entry_price", price))
            tp = float(existing_position.get("tp_target", 0.0)) or (ep + self.take_profit_atr_mult * atr)
            high_px = float(last["high"])
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
                    exit_reason=f"VWAP Reversion trailing stop hit (${stop:.2f})",
                )
            if tp > ep and high_px >= tp:
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=datetime.now(timezone.utc),
                    direction=SignalDirection.FLAT,
                    status=SignalStatus.EXIT,
                    reference_price=max(price, tp),
                    atr=atr,
                    suggested_stop=stop,
                    exit_reason=f"VWAP Reversion take-profit target hit (${tp:.2f})",
                )
            peak_px = max(price, high_px, float(existing_position.get("highest_price", price)))
            new_stop = max(stop, price - self.atr_multiplier * atr)
            if (peak_px - ep) >= (1.2 * atr):
                new_stop = max(new_stop, ep * 1.0015)
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

        if bool(last.get("vwap_entry", False)):
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
                    "vwap": float(last["vwap"]) if pd.notna(last["vwap"]) else price,
                    "rsi": float(last["rsi"]) if pd.notna(last["rsi"]) else 0.0,
                    "atr": atr,
                },
            )

        return create_no_trade_signal(self.STRATEGY_ID, symbol, price, "No VWAP reversion setup")
