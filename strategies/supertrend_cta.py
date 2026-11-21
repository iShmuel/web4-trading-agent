"""ATR Supertrend CTA Trend-Following Strategy (10th Built-In Strategy Engine).

Institutional CTA long/short trend follower that rides 2-5 day micro-trends
on individual crypto and equity assets while the broader market chops sideways.
- Computes Wilder ATR(10) Supertrend bands (multiplier 3.0 crypto / 2.5 equities).
- Enters LONG on bearish->bullish Supertrend flip, SHORT on bullish->bearish flip.
- Rides the full trend using the Supertrend line as a trailing stop and exits on
  an opposite flip or stop-loss breach.
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

MIN_BARS_REQUIRED = 40


def compute_supertrend_indicators(
    df: pd.DataFrame,
    atr_period: int = 10,
    mult: float = 3.0,
    stop_atr_period: int = 14,
) -> pd.DataFrame:
    """Compute causal Wilder ATR Supertrend direction (+1/-1), line, and ATR(14)."""
    out = df.copy()
    h = out["high"].astype(float)
    l = out["low"].astype(float)
    c = out["close"].astype(float)

    tr = pd.concat(
        [(h - l), (h - c.shift(1)).abs(), (l - c.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr_st = tr.ewm(alpha=1.0 / max(2, int(atr_period)), adjust=False).mean()
    atr_stop = tr.ewm(alpha=1.0 / max(2, int(stop_atr_period)), adjust=False).mean()

    hl2 = (h + l) / 2.0
    upper = (hl2 + float(mult) * atr_st).values
    lower = (hl2 - float(mult) * atr_st).values
    cv = c.values
    n = len(cv)

    direction = np.ones(n, dtype=int)
    f_upper = upper.copy()
    f_lower = lower.copy()
    st_line = np.full(n, np.nan, dtype=float)

    for i in range(1, n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            direction[i] = direction[i - 1]
            continue
        if np.isnan(f_upper[i - 1]):
            f_upper[i] = upper[i]
        else:
            f_upper[i] = upper[i] if (upper[i] < f_upper[i - 1] or cv[i - 1] > f_upper[i - 1]) else f_upper[i - 1]

        if np.isnan(f_lower[i - 1]):
            f_lower[i] = lower[i]
        else:
            f_lower[i] = lower[i] if (lower[i] > f_lower[i - 1] or cv[i - 1] < f_lower[i - 1]) else f_lower[i - 1]

        if direction[i - 1] == 1:
            direction[i] = -1 if cv[i] < f_lower[i] else 1
        else:
            direction[i] = 1 if cv[i] > f_upper[i] else -1

        st_line[i] = f_lower[i] if direction[i] == 1 else f_upper[i]

    out["atr"] = atr_stop
    out["supertrend_dir"] = direction
    out["supertrend_line"] = st_line
    out["supertrend_upper"] = f_upper
    out["supertrend_lower"] = f_lower
    return out


class SupertrendCtaStrategy:
    """ATR Supertrend CTA Long/Short Trend-Following Engine."""

    STRATEGY_ID = "supertrend_cta"

    def __init__(self, config: Dict[str, Any]):
        self.atr_period = int(config.get("supertrend_atr_period", 10))
        self.crypto_mult = float(config.get("supertrend_crypto_mult", config.get("supertrend_mult", 3.0)))
        self.equity_mult = float(config.get("supertrend_equity_mult", 2.5))
        self.stop_atr_mult = float(config.get("supertrend_stop_atr_mult", 3.0))
        self.allow_long = bool(config.get("supertrend_allow_long", True))
        self.allow_short = bool(config.get("supertrend_allow_short", True))
        self.fee_pct = float(config.get("fee_pct", 0.0006))
        self.slippage_pct = float(config.get("slippage_pct", 0.0004))
        self.max_stale_minutes = int(config.get("max_stale_minutes", 180))

    def _mult_for_symbol(self, symbol: str) -> float:
        return self.crypto_mult if is_crypto_symbol(symbol) else self.equity_mult

    def evaluate(
        self,
        symbol: str,
        df: pd.DataFrame,
        existing_position: Optional[Dict[str, Any]] = None,
    ) -> TradingSignal:
        """Evaluate closed bars for a Supertrend CTA flip entry or trailing exit."""
        now = datetime.now(timezone.utc)
        raw_px = float(df["close"].iloc[-1]) if (df is not None and not df.empty) else 0.0
        min_req = max(MIN_BARS_REQUIRED, self.atr_period + 15)
        if df is None or len(df) < min_req:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, "Insufficient data for Supertrend CTA")

        closed_df = filter_closed_bars(df)
        stale_limit = self.max_stale_minutes if is_crypto_symbol(symbol) else 0
        ok, reason = check_data_quality(closed_df, max_stale_minutes=stale_limit, min_bars=min_req)
        if not ok:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, reason)

        mult = self._mult_for_symbol(symbol)
        ind = compute_supertrend_indicators(closed_df, atr_period=self.atr_period, mult=mult)
        last = ind.iloc[-1]
        prev = ind.iloc[-2]

        price = float(last["close"])
        atr = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
        cur_dir = int(last["supertrend_dir"])
        prev_dir = int(prev["supertrend_dir"])
        st_line = float(last["supertrend_line"]) if pd.notna(last["supertrend_line"]) else price

        if atr <= 0 or price <= 0:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, price, "Invalid Supertrend ATR/price")

        indicators = {
            "supertrend_dir": cur_dir,
            "supertrend_line": round(st_line, 4),
            "supertrend_mult": mult,
            "atr": round(atr, 4),
        }

        # Existing position management
        if existing_position is not None:
            side = str(existing_position.get("side", "LONG")).upper()
            cur_stop = float(existing_position.get("trailing_stop", existing_position.get("stop_loss", 0.0)) or 0.0)
            if side == "LONG":
                if cur_dir == -1:
                    return TradingSignal(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        timestamp=now,
                        direction=SignalDirection.LONG,
                        status=SignalStatus.EXIT,
                        reference_price=price,
                        exit_reason=f"Supertrend flipped BEARISH (close ${price:.2f} < line ${st_line:.2f})",
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
                        exit_reason=f"Supertrend stop hit (${price:.2f} <= ${cur_stop:.2f})",
                        indicators=indicators,
                    )
                new_stop = max(cur_stop, st_line) if cur_stop > 0 else st_line
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.LONG,
                    status=SignalStatus.HOLD,
                    reference_price=price,
                    suggested_stop=round(new_stop, 6),
                    indicators=indicators,
                )
            else:
                if cur_dir == 1:
                    return TradingSignal(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        timestamp=now,
                        direction=SignalDirection.SHORT,
                        status=SignalStatus.EXIT,
                        reference_price=price,
                        exit_reason=f"Supertrend flipped BULLISH (close ${price:.2f} > line ${st_line:.2f})",
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
                        exit_reason=f"Supertrend short stop hit (${price:.2f} >= ${cur_stop:.2f})",
                        indicators=indicators,
                    )
                new_stop = min(cur_stop, st_line) if cur_stop > 0 else st_line
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.SHORT,
                    status=SignalStatus.HOLD,
                    reference_price=price,
                    suggested_stop=round(new_stop, 6),
                    indicators=indicators,
                )

        # New entry on Supertrend flip
        if cur_dir == 1 and prev_dir == -1 and self.allow_long:
            stop_px = min(price - self.stop_atr_mult * atr, st_line)
            stop_px = max(stop_px, price * 0.5)
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.LONG,
                status=SignalStatus.ENTRY,
                reference_price=price,
                suggested_stop=float(stop_px),
                suggested_take_profit=0.0,
                atr=float(atr),
                signal_strength=0.78,
                estimated_fee_pct=self.fee_pct,
                estimated_slippage_pct=self.slippage_pct,
                indicators=indicators,
            )

        if cur_dir == -1 and prev_dir == 1 and self.allow_short:
            stop_px = max(price + self.stop_atr_mult * atr, st_line)
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.SHORT,
                status=SignalStatus.ENTRY,
                reference_price=price,
                suggested_stop=float(stop_px),
                suggested_take_profit=0.0,
                atr=float(atr),
                signal_strength=0.78,
                estimated_fee_pct=self.fee_pct,
                estimated_slippage_pct=self.slippage_pct,
                indicators=indicators,
            )

        state_txt = "BULLISH" if cur_dir == 1 else "BEARISH"
        return create_no_trade_signal(
            self.STRATEGY_ID,
            symbol,
            price,
            f"Supertrend {state_txt} (no fresh flip)",
            indicators=indicators,
        )
