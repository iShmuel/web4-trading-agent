"""Momentum / Trend Following strategy engine.

Identifies sufficiently strong directional movement using Donchian channel breakout
combined with EMA trend filter. Uses ATR-based trailing stops and optional
tiered scale-out take-profit.

This wraps the existing proven strategy logic from core/strategy.py into a
strategy engine that produces standard TradingSignal objects.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any

import pandas as pd

from core.signals import (
    TradingSignal,
    SignalDirection,
    SignalStatus,
    create_no_trade_signal,
)
from core.strategy import compute_indicators
from core.data_loader import fetch_ohlcv, is_crypto_symbol


# Minimum bars required for reliable indicator calculation
MIN_BARS_REQUIRED = 50


class MomentumStrategy:
    """Donchian breakout + EMA trend filter + ATR volatility sizing.

    Entry: Price breaks above N-period high while above EMA (long),
           or breaks below N-period low while below EMA (short).
    Exit:  ATR trailing stop, with optional partial take-profit.
    """

    STRATEGY_ID = "momentum"

    def __init__(self, config: Dict[str, Any]):
        self.breakout_lookback = config.get("breakout_lookback", 24)
        self.ema_trend_filter = config.get("ema_trend_filter", 50)
        self.atr_period = config.get("atr_period", 14)
        self.atr_multiplier = config.get("atr_multiplier", 3.0)
        self.take_profit_atr_mult = config.get("take_profit_atr_mult", 0.0)
        self.take_profit_ratio = config.get("take_profit_ratio", 0.0)
        self.allow_short = config.get("allow_short", False)
        self.short_requires_macro_bear = config.get("short_requires_macro_bear", True)
        self.fee_pct = config.get("fee_pct", 0.0006)
        self.slippage_pct = config.get("slippage_pct", 0.0004)
        self.min_atr_filter = config.get("min_atr_ratio", 0.0)  # minimum ATR/price ratio
        self.max_stale_minutes = config.get("max_stale_minutes", 180)
        self.macro_ema_period = config.get("macro_ema_period", 200)

    def evaluate(
        self,
        symbol: str,
        df: pd.DataFrame,
        existing_position: Optional[Dict[str, Any]] = None,
    ) -> TradingSignal:
        """Evaluate market data and produce a TradingSignal.

        Args:
            symbol: Asset identifier.
            df: OHLCV DataFrame with datetime index.
            existing_position: Current open position dict if holding, else None.

        Returns:
            TradingSignal with entry/exit/hold/no_trade decision.
        """
        now = datetime.now(timezone.utc)

        # Data quality checks
        if df is None or len(df) < MIN_BARS_REQUIRED:
            return create_no_trade_signal(
                self.STRATEGY_ID, symbol, 0.0,
                f"Insufficient data: {len(df) if df is not None else 0} bars < {MIN_BARS_REQUIRED} minimum",
            )

        # Staleness check
        last_bar_time = df.index[-1]
        if hasattr(last_bar_time, 'tzinfo') and last_bar_time.tzinfo is not None:
            staleness_minutes = (now - last_bar_time).total_seconds() / 60
        else:
            staleness_minutes = 0  # can't check without tz info

        if self.max_stale_minutes > 0 and staleness_minutes > self.max_stale_minutes:
            return create_no_trade_signal(
                self.STRATEGY_ID, symbol, float(df.iloc[-1]["close"]),
                f"Stale data: {staleness_minutes:.0f}m old > {self.max_stale_minutes}m limit",
            )

        # Compute indicators (or reuse precomputed columns during fast replay)
        if "_precomputed" in df.columns:
            data = df
        else:
            data = compute_indicators(
                df,
                breakout_lookback=self.breakout_lookback,
                ema_trend_filter=self.ema_trend_filter,
                atr_period=self.atr_period,
                macro_ema_period=self.macro_ema_period,
            )

        latest = data.iloc[-1]
        price = float(latest["close"])
        atr = float(latest["atr"])
        ema = float(latest["ema_filter"])
        macro_ema = float(latest["macro_ema"]) if "macro_ema" in latest and not pd.isna(latest["macro_ema"]) else ema
        adx = float(latest["adx"]) if "adx" in latest and not pd.isna(latest["adx"]) else 25.0
        breakout_high = float(latest["breakout_high"])
        breakdown_low = float(latest.get("breakdown_low", 0.0))

        if pd.isna(atr) or pd.isna(ema) or pd.isna(breakout_high):
            return create_no_trade_signal(
                self.STRATEGY_ID, symbol, price,
                "Indicators not yet computed (warmup period)",
            )

        # ATR filter: skip low-volatility regimes
        atr_ratio = atr / price if price > 0 else 0
        if self.min_atr_filter > 0 and atr_ratio < self.min_atr_filter:
            return create_no_trade_signal(
                self.STRATEGY_ID, symbol, price,
                f"Low volatility: ATR/price ratio {atr_ratio:.4f} < {self.min_atr_filter}",
            )

        indicators = {
            "price": price,
            "atr": atr,
            "ema_filter": ema,
            "macro_ema": macro_ema,
            "adx": adx,
            "breakout_high": breakout_high,
            "breakdown_low": breakdown_low,
            "atr_ratio": atr_ratio,
        }

        in_uptrend = price > ema
        in_downtrend = price < ema

        # --- If holding a position: evaluate exit/hold ---
        if existing_position is not None:
            return self._evaluate_exit(symbol, price, atr, existing_position, indicators)

        # --- No position: evaluate entry ---

        # Long entry
        if in_uptrend and price > breakout_high:
            stop = max(0.0, price - (atr * self.atr_multiplier))
            tp = price + (atr * self.take_profit_atr_mult) if self.take_profit_atr_mult > 0 else 0.0

            # Estimate net expectancy
            risk_per_unit = price - stop
            reward_per_unit = tp - price if tp > 0 else risk_per_unit * 2  # assume 2R if no TP
            cost_per_unit = price * self.estimated_cost_pct
            net_reward = reward_per_unit - cost_per_unit

            if net_reward <= 0:
                return create_no_trade_signal(
                    self.STRATEGY_ID, symbol, price,
                    f"Costs eliminate edge: net reward {net_reward:.2f} <= 0",
                )

            signal_strength = min(1.0, (price - ema) / atr) if atr > 0 else 0.5

            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.LONG,
                status=SignalStatus.ENTRY,
                reference_price=price,
                atr=atr,
                signal_strength=signal_strength,
                suggested_stop=stop,
                suggested_take_profit=tp,
                take_profit_ratio=self.take_profit_ratio,
                estimated_fee_pct=self.fee_pct,
                estimated_slippage_pct=self.slippage_pct,
                indicators=indicators,
            )

        # Short entry
        macro_bear_ok = (not self.short_requires_macro_bear) or (price < macro_ema)
        if self.allow_short and in_downtrend and macro_bear_ok and breakdown_low > 0 and price < breakdown_low:
            stop = price + (atr * self.atr_multiplier)
            tp = price - (atr * self.take_profit_atr_mult) if self.take_profit_atr_mult > 0 else 0.0

            signal_strength = min(1.0, (ema - price) / atr) if atr > 0 else 0.5

            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.SHORT,
                status=SignalStatus.ENTRY,
                reference_price=price,
                atr=atr,
                signal_strength=signal_strength,
                suggested_stop=stop,
                suggested_take_profit=tp,
                take_profit_ratio=self.take_profit_ratio,
                estimated_fee_pct=self.fee_pct,
                estimated_slippage_pct=self.slippage_pct,
                indicators=indicators,
            )

        # No signal
        dist_to_breakout = ((breakout_high - price) / price * 100) if price > 0 else 0
        trend_str = "uptrend" if in_uptrend else "downtrend"
        return create_no_trade_signal(
            self.STRATEGY_ID, symbol, price,
            f"No breakout: price {dist_to_breakout:+.2f}% from breakout high, in {trend_str}",
            indicators=indicators,
        )

    @property
    def estimated_cost_pct(self) -> float:
        return (self.fee_pct + self.slippage_pct) * 2

    def _evaluate_exit(
        self,
        symbol: str,
        price: float,
        atr: float,
        position: Dict[str, Any],
        indicators: Dict[str, float],
    ) -> TradingSignal:
        """Evaluate whether to exit, partially exit, or hold an existing position."""
        now = datetime.now(timezone.utc)
        entry_price = position["entry_price"]
        trailing_stop = position["trailing_stop"]
        side = position.get("side", "LONG")
        tp_hit = position.get("tp_hit", False)
        tp_target = position.get("tp_target", 0.0)

        # Check trailing stop hit
        if side == "LONG" and price <= trailing_stop:
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.LONG,
                status=SignalStatus.EXIT,
                reference_price=price,
                atr=atr,
                exit_reason="ATR Trailing Stop Hit",
                indicators=indicators,
            )

        if side == "SHORT" and price >= trailing_stop:
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.SHORT,
                status=SignalStatus.EXIT,
                reference_price=price,
                atr=atr,
                exit_reason="ATR Trailing Stop Hit",
                indicators=indicators,
            )

        # Partial take-profit: scale out once price reaches the ATR target set at entry
        tp_ratio = position.get("tp_ratio", self.take_profit_ratio)
        if not tp_hit and tp_target > 0 and tp_ratio > 0:
            reached = price >= tp_target if side == "LONG" else price <= tp_target
            if reached:
                return TradingSignal(
                    strategy_id=self.STRATEGY_ID,
                    symbol=symbol,
                    timestamp=now,
                    direction=SignalDirection.LONG if side == "LONG" else SignalDirection.SHORT,
                    status=SignalStatus.PARTIAL_EXIT,
                    reference_price=price,
                    atr=atr,
                    take_profit_ratio=tp_ratio,
                    exit_reason=f"Momentum Partial Take-Profit ({tp_ratio*100:.0f}% at {tp_target:.2f})",
                    indicators=indicators,
                )

        # Hold
        return TradingSignal(
            strategy_id=self.STRATEGY_ID,
            symbol=symbol,
            timestamp=now,
            direction=SignalDirection.LONG if side == "LONG" else SignalDirection.SHORT,
            status=SignalStatus.HOLD,
            reference_price=price,
            atr=atr,
            indicators=indicators,
        )
