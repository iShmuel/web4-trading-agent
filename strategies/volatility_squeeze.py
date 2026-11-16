"""Volatility Squeeze Breakout strategy engine.

Detects volatility compression where Bollinger Bands contract inside Keltner Channels
("coiled spring") and enters LONG on high-volume upper-band expansion above EMA50.
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


def compute_squeeze_indicators(
    df: pd.DataFrame,
    bb_period: int = 20,
    bb_std: float = 2.0,
    kc_mult: float = 1.5,
    atr_period: int = 14,
    ema_period: int = 50,
    squeeze_window: int = 4,
    min_squeeze_bars: int = 3,
    vol_mult: float = 1.2,
) -> pd.DataFrame:
    """Compute causal Bollinger/Keltner Squeeze, ATR, EMA50, and volume confirmation."""
    out = df.copy()
    c = out["close"].astype(float)
    h = out["high"].astype(float)
    l = out["low"].astype(float)
    v = out["volume"].astype(float) if "volume" in out.columns else pd.Series(1.0, index=out.index)

    sma = c.rolling(bb_period).mean()
    std = c.rolling(bb_period).std()
    out["bb_upper"] = sma + bb_std * std
    out["bb_lower"] = sma - bb_std * std

    tr = pd.concat([(h - l), (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(atr_period).mean()

    kc_upper = sma + kc_mult * out["atr"]
    kc_lower = sma - kc_mult * out["atr"]
    in_squeeze = (out["bb_upper"] < kc_upper) & (out["bb_lower"] > kc_lower)
    out["squeeze_active"] = in_squeeze.rolling(squeeze_window).sum() >= min_squeeze_bars
    out["ema_filter"] = c.ewm(span=ema_period, adjust=False).mean()
    out["vol_sma"] = v.rolling(bb_period).mean()
    out["vol_ratio"] = v / out["vol_sma"].replace(0.0, np.nan)
    out["squeeze_breakout"] = (
        out["squeeze_active"].shift(1).fillna(False)
        & (c > out["bb_upper"])
        & (c > out["ema_filter"])
        & (v > out["vol_sma"] * vol_mult)
    )
    return out


class VolatilitySqueezeStrategy:
    """Bollinger/Keltner Volatility Squeeze Breakout Strategy."""

    STRATEGY_ID = "volatility_squeeze"

    def __init__(self, config: Dict[str, Any]):
        self.bb_period = int(config.get("squeeze_bb_period", 20))
        self.bb_std = float(config.get("squeeze_bb_std", 2.0))
        self.kc_mult = float(config.get("squeeze_kc_mult", 1.5))
        self.atr_period = int(config.get("atr_period", 14))
        self.atr_multiplier = float(config.get("squeeze_atr_stop_mult", 2.0))
        self.take_profit_atr_mult = float(config.get("squeeze_tp_atr_mult", 2.5))
        self.vol_mult = float(config.get("squeeze_vol_mult", 1.2))
        self.fee_pct = float(config.get("maker_fee_pct", config.get("fee_pct", 0.0002)))
        self.slippage_pct = float(config.get("slippage_pct", 0.0001))
        self.max_stale_minutes = int(config.get("max_stale_minutes", 180))

    def evaluate(
        self,
        symbol: str,
        df: pd.DataFrame,
        existing_position: Optional[Dict[str, Any]] = None,
    ) -> TradingSignal:
        """Evaluate closed bars for a volatility squeeze expansion entry or trailing exit."""
        raw_px = float(df["close"].iloc[-1]) if (df is not None and not df.empty) else 0.0
        if df is None or len(df) < MIN_BARS_REQUIRED:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, "Insufficient data for squeeze")

        closed_df = filter_closed_bars(df)
        ok, reason = check_data_quality(closed_df, max_stale_minutes=self.max_stale_minutes, min_bars=MIN_BARS_REQUIRED)
        if not ok:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, reason)

        ind = compute_squeeze_indicators(
            closed_df,
            bb_period=self.bb_period,
            bb_std=self.bb_std,
            kc_mult=self.kc_mult,
            atr_period=self.atr_period,
            vol_mult=self.vol_mult,
        )
        last = ind.iloc[-1]
        price = float(last["close"])
        atr = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
        if atr <= 0 or price <= 0:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, price, "Invalid ATR/price for squeeze")

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
                    exit_reason=f"Volatility Squeeze trailing stop hit (${stop:.2f})",
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

        if bool(last.get("squeeze_breakout", False)):
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
                    "bb_upper": float(last["bb_upper"]),
                    "ema_filter": float(last["ema_filter"]),
                    "atr": atr,
                },
            )

        return create_no_trade_signal(self.STRATEGY_ID, symbol, price, "No volatility squeeze breakout")
