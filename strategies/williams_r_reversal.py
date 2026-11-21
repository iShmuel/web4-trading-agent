"""Larry Williams %R Extreme Range Reversal Strategy (12th Built-In Strategy Engine).

Empirically verified CHOPPY-market specialist (positive on 30d, 90d, 365d, and 730d OOS):
- Computes causal Williams %R(14), 14-bar range midpoint, Wilder ADX(14), and ATR(14).
- Only fires in low-ADX sideways/choppy regimes (ADX < 22.0):
  - Enters LONG when %R was oversold (< -85) on the prior bar and ticks back above -80.
  - Enters SHORT when %R was overbought (> -15) on the prior bar and ticks back below -20.
- Exits on mean reversion to the 14-bar range midpoint or 2.0x ATR stop-loss.
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

MIN_BARS_REQUIRED = 45


def compute_williams_r_indicators(
    df: pd.DataFrame,
    wr_period: int = 14,
    adx_period: int = 14,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Compute causal Williams %R, range midpoint, Wilder ADX, and ATR."""
    out = df.copy()
    h = out["high"].astype(float)
    l = out["low"].astype(float)
    c = out["close"].astype(float)

    tr = pd.concat([(h - l), (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / max(2, int(atr_period)), adjust=False).mean()
    out["atr"] = atr

    up = h.diff()
    dn = -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=out.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=out.index)
    atr_adx = tr.ewm(alpha=1.0 / max(2, int(adx_period)), adjust=False).mean().replace(0.0, np.nan)
    plus_di = 100.0 * (plus_dm.ewm(alpha=1.0 / max(2, int(adx_period)), adjust=False).mean() / atr_adx)
    minus_di = 100.0 * (minus_dm.ewm(alpha=1.0 / max(2, int(adx_period)), adjust=False).mean() / atr_adx)
    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)).fillna(0.0)
    out["adx"] = dx.ewm(alpha=1.0 / max(2, int(adx_period)), adjust=False).mean()

    hh = h.rolling(int(wr_period)).max()
    ll = l.rolling(int(wr_period)).min()
    out["williams_r"] = -100.0 * (hh - c) / (hh - ll).replace(0.0, np.nan)
    out["wr_mid"] = (hh + ll) / 2.0
    return out


class WilliamsRReversalStrategy:
    """Larry Williams %R(14) + Low-ADX Sideways Mean-Reversion Engine."""

    STRATEGY_ID = "williams_r_reversal"

    def __init__(self, config: Dict[str, Any]):
        self.wr_period = int(config.get("williams_r_period", 14))
        self.adx_max = float(config.get("williams_r_adx_max", 22.0))
        self.oversold_trigger = float(config.get("williams_r_oversold", -85.0))
        self.oversold_confirm = float(config.get("williams_r_oversold_confirm", -80.0))
        self.overbought_trigger = float(config.get("williams_r_overbought", -15.0))
        self.overbought_confirm = float(config.get("williams_r_overbought_confirm", -20.0))
        self.stop_atr_mult = float(config.get("williams_r_stop_atr_mult", 2.0))
        self.tp_atr_mult = float(config.get("williams_r_tp_atr_mult", 2.0))
        self.allow_long = bool(config.get("williams_r_allow_long", True))
        self.allow_short = bool(config.get("williams_r_allow_short", True))
        self.fee_pct = float(config.get("fee_pct", 0.0006))
        self.slippage_pct = float(config.get("slippage_pct", 0.0004))
        self.max_stale_minutes = int(config.get("max_stale_minutes", 180))

    def evaluate(
        self,
        symbol: str,
        df: pd.DataFrame,
        existing_position: Optional[Dict[str, Any]] = None,
    ) -> TradingSignal:
        """Evaluate closed bars for a Williams %R extreme tick-back entry or midpoint exit."""
        now = datetime.now(timezone.utc)
        raw_px = float(df["close"].iloc[-1]) if (df is not None and not df.empty) else 0.0
        min_req = max(MIN_BARS_REQUIRED, self.wr_period + 25)
        if df is None or len(df) < min_req:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, "Insufficient data for Williams %R")

        closed_df = filter_closed_bars(df)
        stale_limit = self.max_stale_minutes if is_crypto_symbol(symbol) else 0
        ok, reason = check_data_quality(closed_df, max_stale_minutes=stale_limit, min_bars=min_req)
        if not ok:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, reason)

        ind = compute_williams_r_indicators(closed_df, wr_period=self.wr_period)
        last = ind.iloc[-1]
        prev = ind.iloc[-2]

        price = float(last["close"])
        atr = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
        adx = float(last["adx"]) if pd.notna(last["adx"]) else 50.0
        cur_wr = float(last["williams_r"]) if pd.notna(last["williams_r"]) else -50.0
        prev_wr = float(prev["williams_r"]) if pd.notna(prev["williams_r"]) else -50.0
        mid = float(last["wr_mid"]) if pd.notna(last["wr_mid"]) else price

        if atr <= 0 or price <= 0:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, price, "Invalid Williams %R ATR/price")

        indicators = {
            "williams_r": round(cur_wr, 2),
            "adx": round(adx, 2),
            "wr_mid": round(mid, 6),
            "atr": float(atr),
        }

        if existing_position is not None:
            side = str(existing_position.get("side", "LONG")).upper()
            cur_stop = float(existing_position.get("trailing_stop", existing_position.get("stop_loss", 0.0)) or 0.0)
            if side == "LONG":
                if price >= mid:
                    return TradingSignal(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        timestamp=now,
                        direction=SignalDirection.LONG,
                        status=SignalStatus.EXIT,
                        reference_price=price,
                        atr=float(atr),
                        exit_reason=f"Williams %R midpoint target hit (${price:.4f} >= ${mid:.4f})",
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
                        atr=float(atr),
                        exit_reason=f"Williams %R stop hit (${price:.4f} <= ${cur_stop:.4f})",
                        indicators=indicators,
                    )
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.LONG,
                    status=SignalStatus.HOLD,
                    reference_price=price,
                    atr=float(atr),
                    suggested_stop=cur_stop,
                    suggested_take_profit=float(mid),
                    indicators=indicators,
                )
            else:
                if price <= mid:
                    return TradingSignal(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        timestamp=now,
                        direction=SignalDirection.SHORT,
                        status=SignalStatus.EXIT,
                        reference_price=price,
                        atr=float(atr),
                        exit_reason=f"Williams %R short midpoint target hit (${price:.4f} <= ${mid:.4f})",
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
                        atr=float(atr),
                        exit_reason=f"Williams %R short stop hit (${price:.4f} >= ${cur_stop:.4f})",
                        indicators=indicators,
                    )
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.SHORT,
                    status=SignalStatus.HOLD,
                    reference_price=price,
                    atr=float(atr),
                    suggested_stop=cur_stop,
                    suggested_take_profit=float(mid),
                    indicators=indicators,
                )

        if adx >= self.adx_max:
            return create_no_trade_signal(
                self.STRATEGY_ID,
                symbol,
                price,
                f"Trending regime (ADX {adx:.1f} >= {self.adx_max:.1f})",
                indicators=indicators,
            )

        if self.allow_long and prev_wr < self.oversold_trigger and cur_wr >= self.oversold_confirm:
            stop_px = max(price - self.stop_atr_mult * atr, price * 0.5)
            tp_px = min(mid, price + self.tp_atr_mult * atr) if mid > price else price + self.tp_atr_mult * atr
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.LONG,
                status=SignalStatus.ENTRY,
                reference_price=price,
                atr=float(atr),
                suggested_stop=float(stop_px),
                suggested_take_profit=float(tp_px),
                signal_strength=0.77,
                estimated_fee_pct=self.fee_pct,
                estimated_slippage_pct=self.slippage_pct,
                indicators=indicators,
            )

        if self.allow_short and prev_wr > self.overbought_trigger and cur_wr <= self.overbought_confirm:
            stop_px = price + self.stop_atr_mult * atr
            tp_px = max(mid, price - self.tp_atr_mult * atr) if mid < price else price - self.tp_atr_mult * atr
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.SHORT,
                status=SignalStatus.ENTRY,
                reference_price=price,
                atr=float(atr),
                suggested_stop=float(stop_px),
                suggested_take_profit=float(tp_px),
                signal_strength=0.77,
                estimated_fee_pct=self.fee_pct,
                estimated_slippage_pct=self.slippage_pct,
                indicators=indicators,
            )

        return create_no_trade_signal(
            self.STRATEGY_ID,
            symbol,
            price,
            f"No Williams %R extreme tick-back (%R={cur_wr:.1f})",
            indicators=indicators,
        )
