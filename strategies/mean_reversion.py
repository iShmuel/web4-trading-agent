"""Mean Reversion strategy engine.

Identifies statistically meaningful deviations from a reference mean where a
return toward that reference is plausible. Uses Bollinger Bands and RSI to
detect oversold/overbought conditions in range-bound markets.

Entry: Price touches or pierces a Bollinger Band while RSI confirms
       oversold/overbought, and there is no strong directional trend (ADX filter).
Exit:  Price returns toward the moving average, or stop-loss/max-hold triggers.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd

from core.signals import (
    TradingSignal,
    SignalDirection,
    SignalStatus,
    create_no_trade_signal,
)
from core.strategy import calculate_atr, compute_mean_reversion_indicators


# Minimum bars required for reliable indicator calculation
MIN_BARS_REQUIRED = 50


class MeanReversionStrategy:
    """Bollinger Band + RSI mean reversion with ADX trend filter.

    Entry (Long): Price at or below lower BB, RSI < oversold threshold,
                  ADX below trend threshold (range-bound market).
    Entry (Short): Price at or above upper BB, RSI > overbought threshold,
                   ADX below trend threshold.
    Exit:  Price returns toward BB SMA (mean), or stop-loss, or max holding bars.
    """

    STRATEGY_ID = "mean_reversion"

    def __init__(self, config: Dict[str, Any]):
        self.bb_period = config.get("bb_period", 20)
        self.bb_std = config.get("bb_std", 2.0)
        self.rsi_period = config.get("rsi_period", 14)
        self.rsi_oversold = config.get("rsi_oversold", 30)
        self.rsi_overbought = config.get("rsi_overbought", 70)
        self.atr_period = config.get("atr_period", 14)
        self.atr_stop_mult = config.get("mr_atr_stop_mult", 2.0)
        self.adx_period = config.get("adx_period", 14)
        self.adx_threshold = config.get("adx_threshold", 25)  # below this = range-bound
        self.max_hold_bars = config.get("mr_max_hold_bars", 48)  # max holding period
        self.allow_short = config.get("mr_allow_short", False)
        self.fee_pct = config.get("fee_pct", 0.0006)
        self.slippage_pct = config.get("slippage_pct", 0.0004)
        self.min_bb_width = config.get("min_bb_width", 0.005)  # minimum band width
        self.max_stale_minutes = config.get("max_stale_minutes", 180)
        self.scale_out_enabled = config.get("mr_scale_out_enabled", True)
        self.scale_out_ratio = config.get("mr_scale_out_ratio", 0.5)
        self.enable_liquidity_sweep = config.get("enable_liquidity_sweep", True)
        self.sweep_lookback = int(config.get("sweep_lookback", 20))
        self.sweep_rsi_max = float(config.get("sweep_rsi_max", 40.0))

    def evaluate(
        self,
        symbol: str,
        df: pd.DataFrame,
        existing_position: Optional[Dict[str, Any]] = None,
    ) -> TradingSignal:
        """Evaluate market data and produce a TradingSignal."""
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
            staleness_minutes = 0

        if self.max_stale_minutes > 0 and staleness_minutes > self.max_stale_minutes:
            return create_no_trade_signal(
                self.STRATEGY_ID, symbol, float(df.iloc[-1]["close"]),
                f"Stale data: {staleness_minutes:.0f}m old > {self.max_stale_minutes}m limit",
            )

        # Compute indicators (or reuse precomputed columns during fast replay)
        if "_precomputed" in df.columns:
            data = df
        else:
            data = compute_mean_reversion_indicators(
                df,
                bb_period=self.bb_period,
                bb_std=self.bb_std,
                rsi_period=self.rsi_period,
                atr_period=self.atr_period,
                adx_period=self.adx_period,
            )

        latest = data.iloc[-1]
        price = float(latest["close"])
        atr = float(latest["atr"])
        rsi = float(latest["rsi"])
        bb_sma = float(latest["bb_sma"])
        bb_upper = float(latest["bb_upper"])
        bb_lower = float(latest["bb_lower"])
        bb_width = float(latest["bb_width"])
        z_score = float(latest["z_score"])
        adx = float(latest["adx"])

        if pd.isna(atr) or pd.isna(rsi) or pd.isna(bb_sma) or pd.isna(adx):
            return create_no_trade_signal(
                self.STRATEGY_ID, symbol, price,
                "Indicators not yet computed (warmup period)",
            )

        indicators = {
            "price": price,
            "atr": atr,
            "rsi": rsi,
            "bb_sma": bb_sma,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "bb_width": bb_width,
            "z_score": z_score,
            "adx": adx,
        }

        # --- If holding a position: evaluate exit/hold ---
        if existing_position is not None:
            return self._evaluate_exit(symbol, price, atr, rsi, bb_sma, existing_position, indicators, data)

        # --- No position: evaluate entry ---

        # Filter: market must be range-bound (low ADX)
        if adx > self.adx_threshold:
            return create_no_trade_signal(
                self.STRATEGY_ID, symbol, price,
                f"Strong trend detected: ADX {adx:.1f} > {self.adx_threshold} threshold",
                indicators=indicators,
            )

        # Filter: bands too narrow (low volatility, not worth trading)
        if bb_width < self.min_bb_width:
            return create_no_trade_signal(
                self.STRATEGY_ID, symbol, price,
                f"Bands too narrow: width {bb_width:.4f} < {self.min_bb_width}",
                indicators=indicators,
            )

        # Long entry: oversold condition
        if price <= bb_lower and rsi < self.rsi_oversold:
            stop = max(0.0, price - (atr * self.atr_stop_mult))
            # Target: return to SMA (the mean)
            tp = bb_sma

            # Cost check
            reward = tp - price
            risk = price - stop
            cost = price * self.estimated_cost_pct
            net_reward = reward - cost

            if net_reward <= 0 or risk <= 0:
                return create_no_trade_signal(
                    self.STRATEGY_ID, symbol, price,
                    f"Costs eliminate edge: net reward {net_reward:.2f}, risk {risk:.2f}",
                )

            # Signal strength based on z-score depth
            signal_strength = min(1.0, abs(z_score) / 3.0)

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
                estimated_fee_pct=self.fee_pct,
                estimated_slippage_pct=self.slippage_pct,
                indicators=indicators,
            )

        # Long entry #2: Bullish Liquidity Sweep-and-Reclaim (Stop-Hunt below prior N-bar low & green reclaim)
        if self.enable_liquidity_sweep and len(data) >= self.sweep_lookback + 2 and rsi < self.sweep_rsi_max:
            low_s = data["low"].astype(float)
            open_p = float(latest["open"])
            cur_low = float(latest["low"])
            prior_low_cur = float(low_s.iloc[-(self.sweep_lookback + 1) : -1].min())
            prior_low_prev = float(low_s.iloc[-(self.sweep_lookback + 2) : -2].min())
            prev_low = float(low_s.iloc[-2])

            sweep_cur = (cur_low < prior_low_cur) and (price > prior_low_cur) and (price > open_p)
            sweep_prev = (prev_low < prior_low_prev) and (price > prior_low_prev) and (price > open_p)

            if (sweep_cur or sweep_prev) and bb_sma > price * 1.006:
                stop = max(0.0, min(cur_low - (0.3 * atr), price - (atr * self.atr_stop_mult)))
                tp = bb_sma
                reward = tp - price
                risk = price - stop
                cost = price * self.estimated_cost_pct
                net_reward = reward - cost
                if net_reward > 0 and risk > 0:
                    indicators["liquidity_sweep"] = "BULLISH_SWEEP_RECLAIM"
                    return TradingSignal(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        timestamp=now,
                        direction=SignalDirection.LONG,
                        status=SignalStatus.ENTRY,
                        reference_price=price,
                        atr=atr,
                        signal_strength=0.85,
                        suggested_stop=stop,
                        suggested_take_profit=tp,
                        take_profit_ratio=self.scale_out_ratio if self.scale_out_enabled else 0.0,
                        estimated_fee_pct=self.fee_pct,
                        estimated_slippage_pct=self.slippage_pct,
                        indicators=indicators,
                    )

        # Short entry: overbought condition
        if self.allow_short and price >= bb_upper and rsi > self.rsi_overbought:
            stop = price + (atr * self.atr_stop_mult)
            tp = bb_sma

            reward = price - tp
            risk = stop - price
            cost = price * self.estimated_cost_pct
            net_reward = reward - cost

            if net_reward <= 0 or risk <= 0:
                return create_no_trade_signal(
                    self.STRATEGY_ID, symbol, price,
                    f"Costs eliminate edge: net reward {net_reward:.2f}, risk {risk:.2f}",
                )

            signal_strength = min(1.0, abs(z_score) / 3.0)

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
                estimated_fee_pct=self.fee_pct,
                estimated_slippage_pct=self.slippage_pct,
                indicators=indicators,
            )

        # No signal
        return create_no_trade_signal(
            self.STRATEGY_ID, symbol, price,
            f"No mean reversion setup: RSI {rsi:.1f}, z-score {z_score:.2f}, ADX {adx:.1f}",
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
        rsi: float,
        bb_sma: float,
        position: Dict[str, Any],
        indicators: Dict[str, float],
        data: pd.DataFrame,
    ) -> TradingSignal:
        """Evaluate whether to exit or hold a mean-reversion position."""
        now = datetime.now(timezone.utc)
        entry_price = position["entry_price"]
        trailing_stop = position["trailing_stop"]
        side = position.get("side", "LONG")
        entry_time = position.get("entry_time", "")

        # Stop-loss check
        if side == "LONG" and price <= trailing_stop:
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.LONG,
                status=SignalStatus.EXIT,
                reference_price=price,
                atr=atr,
                exit_reason="Stop Loss Hit",
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
                exit_reason="Stop Loss Hit",
                indicators=indicators,
            )

        # Partial Take-Profit check: scale out 50% halfway to the SMA mean
        tp_hit = position.get("tp_hit", False)
        if self.scale_out_enabled and not tp_hit:
            if side == "LONG":
                halfway_target = entry_price + (bb_sma - entry_price) * 0.5
                if price >= halfway_target:
                    return TradingSignal(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        timestamp=now,
                        direction=SignalDirection.LONG,
                        status=SignalStatus.PARTIAL_EXIT,
                        reference_price=price,
                        atr=atr,
                        take_profit_ratio=self.scale_out_ratio,
                        exit_reason=f"Halfway Scale-Out Take-Profit ({int(self.scale_out_ratio*100)}% Banked)",
                        indicators=indicators,
                    )
            elif side == "SHORT":
                halfway_target = entry_price - (entry_price - bb_sma) * 0.5
                if price <= halfway_target:
                    return TradingSignal(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        timestamp=now,
                        direction=SignalDirection.SHORT,
                        status=SignalStatus.PARTIAL_EXIT,
                        reference_price=price,
                        atr=atr,
                        take_profit_ratio=self.scale_out_ratio,
                        exit_reason=f"Halfway Scale-Out Take-Profit ({int(self.scale_out_ratio*100)}% Banked)",
                        indicators=indicators,
                    )

        # Mean reversion target: price returned to SMA
        if side == "LONG" and price >= bb_sma:
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.LONG,
                status=SignalStatus.EXIT,
                reference_price=price,
                atr=atr,
                exit_reason="Mean Reversion Target (SMA)",
                indicators=indicators,
            )

        if side == "SHORT" and price <= bb_sma:
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.SHORT,
                status=SignalStatus.EXIT,
                reference_price=price,
                atr=atr,
                exit_reason="Mean Reversion Target (SMA)",
                indicators=indicators,
            )

        # Max holding period check
        if entry_time and self.max_hold_bars > 0:
            try:
                entry_dt = pd.Timestamp(entry_time)
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.tz_localize("UTC")
                bars_held = len(data.loc[entry_dt:])
                if bars_held >= self.max_hold_bars:
                    return TradingSignal(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        timestamp=now,
                        direction=SignalDirection.LONG if side == "LONG" else SignalDirection.SHORT,
                        status=SignalStatus.EXIT,
                        reference_price=price,
                        atr=atr,
                        exit_reason=f"Max Holding Period ({self.max_hold_bars} bars)",
                        indicators=indicators,
                    )
            except Exception:
                pass  # can't determine holding period, continue holding

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
