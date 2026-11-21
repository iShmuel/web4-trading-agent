"""Donchian Range Fade + RSI-2 Mean-Reversion Strategy (11th Built-In Strategy Engine).

Empirical Bear-market specialist (#2 of 15 in isolated BEAR OOS tournament):
- Computes causal 20-bar Donchian Channel (shifted by 1 bar), channel width, and RSI(2).
- In compressed/narrow ranges (channel width below its 100-bar 50th percentile):
  - Enters LONG when price pierces/touches the lower Donchian boundary with RSI(2) < 10.
  - Enters SHORT when price pierces/touches the upper Donchian boundary with RSI(2) > 90.
- Exits on mean reversion back to the Donchian midline or 2.0x ATR stop-loss.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from core.data_loader import check_data_quality, filter_closed_bars, is_crypto_symbol
from core.signals import (
    SignalDirection,
    SignalStatus,
    TradingSignal,
    create_no_trade_signal,
)

MIN_BARS_REQUIRED = 125


def compute_donchian_range_fade_indicators(
    df: pd.DataFrame,
    window: int = 20,
    rsi_period: int = 2,
    width_lookback: int = 100,
    width_quantile: float = 0.50,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Compute causal Donchian High/Low/Mid, narrow-width flag, RSI(2), and ATR(14)."""
    out = df.copy()
    h = out["high"].astype(float)
    l = out["low"].astype(float)
    c = out["close"].astype(float)

    delta = c.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / max(1, int(rsi_period)), adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / max(1, int(rsi_period)), adjust=False).mean()
    rs = gain / loss.replace(0.0, np.nan)
    out["rsi2"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    tr = pd.concat([(h - l), (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    out["atr"] = tr.ewm(alpha=1.0 / max(2, int(atr_period)), adjust=False).mean()

    d_hi = h.shift(1).rolling(int(window)).max()
    d_lo = l.shift(1).rolling(int(window)).min()
    d_mid = (d_hi + d_lo) / 2.0
    width = (d_hi - d_lo) / d_mid.replace(0.0, np.nan)
    width_thresh = width.rolling(int(width_lookback)).quantile(float(width_quantile))

    out["donch_high"] = d_hi
    out["donch_low"] = d_lo
    out["donch_mid"] = d_mid
    out["donch_width"] = width
    out["donch_narrow"] = (width < width_thresh).fillna(False)
    return out


class DonchianRangeFadeStrategy:
    """Donchian Channel Boundary + RSI(2) Extreme Mean-Reversion Engine."""

    STRATEGY_ID = "donchian_range_fade"

    def __init__(self, config: Dict[str, Any]):
        self.window = int(config.get("donchian_fade_window", 20))
        self.rsi_period = int(config.get("donchian_fade_rsi_period", 2))
        self.rsi_low = float(config.get("donchian_fade_rsi_low", 10.0))
        self.rsi_high = float(config.get("donchian_fade_rsi_high", 90.0))
        self.width_lookback = int(config.get("donchian_fade_width_lookback", 100))
        self.width_quantile = float(config.get("donchian_fade_width_quantile", 0.50))
        self.stop_atr_mult = float(config.get("donchian_fade_stop_atr_mult", 2.0))
        self.tp_atr_mult = float(config.get("donchian_fade_tp_atr_mult", 2.0))
        self.allow_long = bool(config.get("donchian_fade_allow_long", True))
        self.allow_short = bool(config.get("donchian_fade_allow_short", True))
        self.fee_pct = float(config.get("fee_pct", 0.0006))
        self.slippage_pct = float(config.get("slippage_pct", 0.0004))
        self.max_stale_minutes = int(config.get("max_stale_minutes", 180))

    def evaluate(
        self,
        symbol: str,
        df: pd.DataFrame,
        existing_position: Optional[Dict[str, Any]] = None,
    ) -> TradingSignal:
        """Evaluate closed bars for a Donchian + RSI(2) boundary fade entry or midline exit."""
        now = datetime.now(timezone.utc)
        raw_px = float(df["close"].iloc[-1]) if (df is not None and not df.empty) else 0.0
        min_req = max(MIN_BARS_REQUIRED, self.window + self.width_lookback + 5)
        if df is None or len(df) < min_req:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, "Insufficient data for Donchian Range Fade")

        closed_df = filter_closed_bars(df)
        stale_limit = self.max_stale_minutes if is_crypto_symbol(symbol) else 0
        ok, reason = check_data_quality(closed_df, max_stale_minutes=stale_limit, min_bars=min_req)
        if not ok:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, reason)

        ind = compute_donchian_range_fade_indicators(
            closed_df,
            window=self.window,
            rsi_period=self.rsi_period,
            width_lookback=self.width_lookback,
            width_quantile=self.width_quantile,
        )
        last = ind.iloc[-1]
        price = float(last["close"])
        atr = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
        rsi2 = float(last["rsi2"]) if pd.notna(last["rsi2"]) else 50.0
        d_hi = float(last["donch_high"]) if pd.notna(last["donch_high"]) else np.nan
        d_lo = float(last["donch_low"]) if pd.notna(last["donch_low"]) else np.nan
        d_mid = float(last["donch_mid"]) if pd.notna(last["donch_mid"]) else np.nan
        narrow = bool(last["donch_narrow"])

        if atr <= 0 or price <= 0 or np.isnan(d_hi) or np.isnan(d_lo) or np.isnan(d_mid) or d_hi <= d_lo:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, price, "Invalid Donchian indicators")

        indicators = {
            "donch_high": round(d_hi, 4),
            "donch_low": round(d_lo, 4),
            "donch_mid": round(d_mid, 4),
            "donch_narrow": narrow,
            "rsi2": round(rsi2, 2),
            "atr": round(atr, 4),
        }

        if existing_position is not None:
            side = str(existing_position.get("side", "LONG")).upper()
            cur_stop = float(existing_position.get("trailing_stop", existing_position.get("stop_loss", 0.0)) or 0.0)
            if side == "LONG":
                if price >= d_mid:
                    return TradingSignal(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        timestamp=now,
                        direction=SignalDirection.LONG,
                        status=SignalStatus.EXIT,
                        reference_price=price,
                        exit_reason=f"Donchian midline reversion reached (${price:.2f} >= ${d_mid:.2f})",
                        indicators=indicators,
                    )
                if cur_stop > 0 and price <= cur_stop:
                    return TradingSignal(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        timestamp=now,
                        direction=SignalDirection.LONG,
                        status=SignalStatus.EXIT,
                        reference_price=price,
                        exit_reason=f"Donchian fade stop hit (${price:.2f} <= ${cur_stop:.2f})",
                        indicators=indicators,
                    )
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.LONG,
                    status=SignalStatus.HOLD,
                    reference_price=price,
                    suggested_stop=cur_stop,
                    suggested_take_profit=round(d_mid, 6),
                    indicators=indicators,
                )
            else:
                if price <= d_mid:
                    return TradingSignal(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        timestamp=now,
                        direction=SignalDirection.SHORT,
                        status=SignalStatus.EXIT,
                        reference_price=price,
                        exit_reason=f"Donchian midline reversion reached (${price:.2f} <= ${d_mid:.2f})",
                        indicators=indicators,
                    )
                if cur_stop > 0 and price >= cur_stop:
                    return TradingSignal(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        timestamp=now,
                        direction=SignalDirection.SHORT,
                        status=SignalStatus.EXIT,
                        reference_price=price,
                        exit_reason=f"Donchian fade short stop hit (${price:.2f} >= ${cur_stop:.2f})",
                        indicators=indicators,
                    )
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.SHORT,
                    status=SignalStatus.HOLD,
                    reference_price=price,
                    suggested_stop=cur_stop,
                    suggested_take_profit=round(d_mid, 6),
                    indicators=indicators,
                )

        if not narrow:
            return create_no_trade_signal(
                self.STRATEGY_ID,
                symbol,
                price,
                "Donchian channel too wide (not range-bound)",
                indicators=indicators,
            )

        if self.allow_long and price <= d_lo * 1.002 and rsi2 < self.rsi_low:
            stop_px = max(price - self.stop_atr_mult * atr, price * 0.5)
            tp_px = min(d_mid, price + self.tp_atr_mult * atr) if d_mid > price else price + self.tp_atr_mult * atr
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.LONG,
                status=SignalStatus.ENTRY,
                reference_price=price,
                suggested_stop=round(stop_px, 6),
                suggested_take_profit=round(tp_px, 6),
                signal_strength=0.76,
                estimated_fee_pct=self.fee_pct,
                estimated_slippage_pct=self.slippage_pct,
                indicators=indicators,
            )

        if self.allow_short and price >= d_hi * 0.998 and rsi2 > self.rsi_high:
            stop_px = price + self.stop_atr_mult * atr
            tp_px = max(d_mid, price - self.tp_atr_mult * atr) if d_mid < price else price - self.tp_atr_mult * atr
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.SHORT,
                status=SignalStatus.ENTRY,
                reference_price=price,
                suggested_stop=round(stop_px, 6),
                suggested_take_profit=round(tp_px, 6),
                signal_strength=0.76,
                estimated_fee_pct=self.fee_pct,
                estimated_slippage_pct=self.slippage_pct,
                indicators=indicators,
            )

        return create_no_trade_signal(
            self.STRATEGY_ID,
            symbol,
            price,
            f"No Donchian boundary extreme (RSI2={rsi2:.1f})",
            indicators=indicators,
        )
