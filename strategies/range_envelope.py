"""Rolling Range Envelope ("Average Top & Down" + Net Dollar Floor) strategy engine.

Computes the causal rolling Average Down (10th-percentile low support zone) and
Average Top (90th-percentile high resistance zone) over a trailing window (default 36h).
In range-bound regimes (ADX <= 24):
- Enters LONG near Average Down (RSI < 30) or SHORT near Average Top (RSI > 70)
  ONLY when the distance to the opposite side of the range covers full round-trip
  fees + slippage AND leaves at least `range_min_net_profit_usd` (default $10.00 net)
  with a reward-to-risk ratio >= 0.8x against the 2.0x ATR stop-loss.
- Scales out 50% at the midpoint / net-dollar floor (locking the stop to Break-Even + fees)
  and exits the remaining 50% at the opposite Average Top / Average Down boundary.
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
from core.strategy import compute_mean_reversion_indicators

MIN_BARS_REQUIRED = 60


def compute_range_envelope_indicators(
    df: pd.DataFrame,
    range_window: int = 36,
    low_quantile: float = 0.10,
    high_quantile: float = 0.90,
    rsi_period: int = 14,
    atr_period: int = 14,
    adx_period: int = 14,
) -> pd.DataFrame:
    """Compute causal rolling Average Down (q_low), Average Top (q_high), RSI, ATR, and ADX."""
    out = compute_mean_reversion_indicators(
        df,
        bb_period=max(10, int(range_window)),
        bb_std=2.0,
        rsi_period=rsi_period,
        atr_period=atr_period,
        adx_period=adx_period,
    ).copy()

    l = out["low"].astype(float)
    h = out["high"].astype(float)

    # Shift by 1 bar so entry evaluation on bar i uses causal support/resistance from [i-window, i-1]
    out["avg_down"] = l.rolling(range_window).quantile(low_quantile).shift(1)
    out["avg_top"] = h.rolling(range_window).quantile(high_quantile).shift(1)
    return out


class RangeEnvelopeStrategy:
    """Rolling Range Envelope Strategy (Average Down <-> Average Top with Net Dollar Floor)."""

    STRATEGY_ID = "range_envelope"

    def __init__(self, config: Dict[str, Any]):
        self.range_window = int(config.get("range_window", 36))
        self.low_quantile = float(config.get("range_low_quantile", 0.10))
        self.high_quantile = float(config.get("range_high_quantile", 0.90))
        self.rsi_period = int(config.get("rsi_period", 14))
        self.rsi_oversold = float(config.get("range_rsi_oversold", 30.0))
        self.rsi_overbought = float(config.get("range_rsi_overbought", 70.0))
        self.adx_period = int(config.get("adx_period", 14))
        self.adx_max = float(config.get("range_adx_max", 24.0))
        self.atr_period = int(config.get("atr_period", 14))
        self.atr_stop_mult = float(config.get("range_atr_stop_mult", 2.0))
        self.min_net_profit_usd = float(config.get("range_min_net_profit_usd", 10.0))
        self.min_rr_ratio = float(config.get("range_min_rr_ratio", 0.8))
        self.scale_out_ratio = float(config.get("range_scale_out_ratio", 0.5))
        self.max_hold_bars = int(config.get("range_max_hold_bars", 48))
        self.allow_long = bool(config.get("range_allow_long", True))
        self.allow_short = bool(config.get("range_allow_short", True))
        self.account_risk_pct = max(0.01, min(0.05, float(config.get("account_risk_pct", 0.02))))
        self.reference_equity = float(config.get("initial_capital", 10000.0))
        self.fee_pct = float(config.get("fee_pct", 0.0006))
        self.slippage_pct = float(config.get("slippage_pct", 0.0004))
        self.max_stale_minutes = int(config.get("max_stale_minutes", 180))

    @property
    def round_trip_cost_pct(self) -> float:
        return (2.0 * self.fee_pct) + self.slippage_pct

    def evaluate(
        self,
        symbol: str,
        df: pd.DataFrame,
        existing_position: Optional[Dict[str, Any]] = None,
    ) -> TradingSignal:
        """Evaluate closed bars for a Range Envelope entry, 50% scale-out, or Average Top/Down exit."""
        now = datetime.now(timezone.utc)
        raw_px = float(df["close"].iloc[-1]) if (df is not None and not df.empty) else 0.0
        min_req = max(MIN_BARS_REQUIRED, self.range_window + 10)
        if df is None or len(df) < min_req:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, "Insufficient data for Range Envelope")

        closed_df = filter_closed_bars(df)
        ok, reason = check_data_quality(closed_df, max_stale_minutes=self.max_stale_minutes, min_bars=min_req)
        if not ok:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, raw_px, reason)

        ind = compute_range_envelope_indicators(
            closed_df,
            range_window=self.range_window,
            low_quantile=self.low_quantile,
            high_quantile=self.high_quantile,
            rsi_period=self.rsi_period,
            atr_period=self.atr_period,
            adx_period=self.adx_period,
        )
        last = ind.iloc[-1]
        price = float(last["close"])
        high = float(last["high"])
        low = float(last["low"])
        atr = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
        rsi = float(last["rsi"]) if pd.notna(last["rsi"]) else 50.0
        adx = float(last["adx"]) if pd.notna(last["adx"]) else 50.0
        avg_down = float(last["avg_down"]) if pd.notna(last["avg_down"]) else np.nan
        avg_top = float(last["avg_top"]) if pd.notna(last["avg_top"]) else np.nan

        if atr <= 0 or price <= 0 or np.isnan(avg_down) or np.isnan(avg_top) or avg_top <= avg_down:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, price, "Invalid range envelope indicators")

        indicators = {
            "avg_down": round(avg_down, 4),
            "avg_top": round(avg_top, 4),
            "rsi": round(rsi, 2),
            "adx": round(adx, 2),
            "atr": round(atr, 4),
        }

        # Existing position management
        if existing_position is not None:
            return self._evaluate_exit(symbol, price, high, low, atr, avg_down, avg_top, existing_position, indicators)

        # Range-bound gate
        if adx > self.adx_max:
            return create_no_trade_signal(
                self.STRATEGY_ID,
                symbol,
                price,
                f"Trending regime (ADX {adx:.1f} > {self.adx_max:.1f})",
                indicators=indicators,
            )

        # Estimate position sizing under 1%-5% risk engine rules to verify the +$10 net profit floor
        stop_dist = atr * self.atr_stop_mult
        dollar_risk = self.reference_equity * self.account_risk_pct
        est_units = min(dollar_risk / stop_dist, (self.reference_equity * 0.95) / price)
        est_notional = est_units * price
        if est_notional <= 0:
            return create_no_trade_signal(self.STRATEGY_ID, symbol, price, "Zero estimated notional")

        req_gross_pct = (self.min_net_profit_usd / est_notional) + self.round_trip_cost_pct

        # Long Entry near Average Down
        if self.allow_long and price <= avg_down and rsi < self.rsi_oversold:
            stop_loss = max(0.0, price - stop_dist)
            tp_net_floor = price * (1.0 + req_gross_pct)
            reward = avg_top - price
            risk = price - stop_loss
            if avg_top >= tp_net_floor and risk > 0 and (reward / risk) >= self.min_rr_ratio:
                tp1 = max(tp_net_floor, price + 0.5 * (avg_top - price))
                est_net_usd = (avg_top - price) * est_units - (est_notional * self.round_trip_cost_pct)
                indicators["est_full_net_usd"] = round(est_net_usd, 2)
                indicators["tp1_midpoint"] = round(tp1, 4)
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.LONG,
                    status=SignalStatus.ENTRY,
                    reference_price=price,
                    atr=atr,
                    signal_strength=0.82,
                    suggested_stop=stop_loss,
                    suggested_take_profit=avg_top,
                    take_profit_ratio=self.scale_out_ratio,
                    estimated_fee_pct=self.fee_pct,
                    estimated_slippage_pct=self.slippage_pct,
                    indicators=indicators,
                )
            return create_no_trade_signal(
                self.STRATEGY_ID,
                symbol,
                price,
                f"Range too narrow for +${self.min_net_profit_usd:.0f} net floor (AvgTop ${avg_top:.2f} < req ${tp_net_floor:.2f})",
                indicators=indicators,
            )

        # Short Entry near Average Top
        if self.allow_short and price >= avg_top and rsi > self.rsi_overbought:
            stop_loss = price + stop_dist
            tp_net_floor = price * (1.0 - req_gross_pct)
            reward = price - avg_down
            risk = stop_loss - price
            if avg_down <= tp_net_floor and risk > 0 and (reward / risk) >= self.min_rr_ratio:
                tp1 = min(tp_net_floor, price - 0.5 * (price - avg_down))
                est_net_usd = (price - avg_down) * est_units - (est_notional * self.round_trip_cost_pct)
                indicators["est_full_net_usd"] = round(est_net_usd, 2)
                indicators["tp1_midpoint"] = round(tp1, 4)
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.SHORT,
                    status=SignalStatus.ENTRY,
                    reference_price=price,
                    atr=atr,
                    signal_strength=0.82,
                    suggested_stop=stop_loss,
                    suggested_take_profit=avg_down,
                    take_profit_ratio=self.scale_out_ratio,
                    estimated_fee_pct=self.fee_pct,
                    estimated_slippage_pct=self.slippage_pct,
                    indicators=indicators,
                )
            return create_no_trade_signal(
                self.STRATEGY_ID,
                symbol,
                price,
                f"Range too narrow for +${self.min_net_profit_usd:.0f} net floor (AvgDown ${avg_down:.2f} > req ${tp_net_floor:.2f})",
                indicators=indicators,
            )

        return create_no_trade_signal(
            self.STRATEGY_ID,
            symbol,
            price,
            f"Inside range envelope (Close ${price:.2f} vs AvgDown ${avg_down:.2f} / AvgTop ${avg_top:.2f})",
            indicators=indicators,
        )

    def _evaluate_exit(
        self,
        symbol: str,
        price: float,
        high: float,
        low: float,
        atr: float,
        avg_down: float,
        avg_top: float,
        position: Dict[str, Any],
        indicators: Dict[str, Any],
    ) -> TradingSignal:
        now = datetime.now(timezone.utc)
        side = str(position.get("side", "LONG")).upper()
        ep = float(position.get("entry_price", price))
        stop = float(position.get("trailing_stop", 0.0))
        units = float(position.get("units", 0.0))
        tp_hit = bool(position.get("tp_hit", False))
        notional = max(1.0, units * ep)
        req_gross_pct = (self.min_net_profit_usd / notional) + self.round_trip_cost_pct

        if side == "LONG":
            if low <= stop:
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.LONG,
                    status=SignalStatus.EXIT,
                    reference_price=price,
                    atr=atr,
                    suggested_stop=stop,
                    exit_reason=f"Range Envelope Stop Hit (${stop:.2f})",
                    indicators=indicators,
                )
            tp_net = ep * (1.0 + req_gross_pct)
            tp1 = max(tp_net, ep + 0.5 * (max(avg_top, tp_net) - ep))
            if not tp_hit and self.scale_out_ratio > 0 and high >= tp1:
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.LONG,
                    status=SignalStatus.PARTIAL_EXIT,
                    reference_price=max(price, tp1),
                    atr=atr,
                    take_profit_ratio=self.scale_out_ratio,
                    exit_reason=f"Range Envelope 50% Midpoint/+${self.min_net_profit_usd:.0f} Floor Reached",
                    indicators=indicators,
                )
            if high >= avg_top and avg_top > ep * (1.0 + self.round_trip_cost_pct):
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.LONG,
                    status=SignalStatus.EXIT,
                    reference_price=max(price, avg_top),
                    atr=atr,
                    exit_reason=f"Range Envelope Average Top Reached (${avg_top:.2f})",
                    indicators=indicators,
                )
            new_stop = max(stop, ep * (1.0 + self.round_trip_cost_pct)) if tp_hit else stop
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.LONG,
                status=SignalStatus.HOLD,
                reference_price=price,
                atr=atr,
                suggested_stop=new_stop,
                indicators=indicators,
            )

        # SHORT position management
        if high >= stop and stop > 0:
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.SHORT,
                status=SignalStatus.EXIT,
                reference_price=price,
                atr=atr,
                suggested_stop=stop,
                exit_reason=f"Range Envelope Stop Hit (${stop:.2f})",
                indicators=indicators,
            )
        tp_net = ep * (1.0 - req_gross_pct)
        tp1 = min(tp_net, ep - 0.5 * (ep - min(avg_down, tp_net)))
        if not tp_hit and self.scale_out_ratio > 0 and low <= tp1:
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.SHORT,
                status=SignalStatus.PARTIAL_EXIT,
                reference_price=min(price, tp1),
                atr=atr,
                take_profit_ratio=self.scale_out_ratio,
                exit_reason=f"Range Envelope 50% Midpoint/+${self.min_net_profit_usd:.0f} Floor Reached",
                indicators=indicators,
            )
        if low <= avg_down and avg_down < ep * (1.0 - self.round_trip_cost_pct):
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.SHORT,
                status=SignalStatus.EXIT,
                reference_price=min(price, avg_down),
                atr=atr,
                exit_reason=f"Range Envelope Average Down Reached (${avg_down:.2f})",
                indicators=indicators,
            )
        new_stop = min(stop, ep * (1.0 - self.round_trip_cost_pct)) if (tp_hit and stop > 0) else stop
        return TradingSignal(
            strategy_id=self.STRATEGY_ID,
            symbol=symbol,
            timestamp=now,
            direction=SignalDirection.SHORT,
            status=SignalStatus.HOLD,
            reference_price=price,
            atr=atr,
            suggested_stop=new_stop,
            indicators=indicators,
        )
