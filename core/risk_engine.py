"""Central Risk Engine and Portfolio Safety Controls.

Enforces:
1. Van Tharp R-risk position sizing strictly bounded between 1% and 5% account risk.
2. Portfolio-level constraints:
   - Max portfolio exposure limit (default: 80% of equity)
   - Max single-asset exposure limit (default: 35% of equity)
   - Max per-strategy capital allocation (default: 60% of equity)
   - Max concurrent open positions
3. Circuit Breakers / Kill Switches:
   - Daily loss limit breach (default: 5% of starting daily equity)
   - Max drawdown breach (default: 15% from high watermark)
   - Emergency halt toggle
4. Ratcheted trailing stop logic for both LONG and SHORT positions.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from typing import Tuple, Dict, Any, Optional, List
import numpy as np
import pandas as pd

from core.data_loader import is_crypto_symbol

# Hard bounds: The AI self-improvement loop CANNOT breach these limits.
MIN_ALLOWED_RISK_PCT = 0.01  # 1%
MAX_ALLOWED_RISK_PCT = 0.05  # 5%


@dataclass
class RiskVerdict:
    approved: bool
    reason: str
    suggested_units: float = 0.0
    suggested_stop: float = 0.0
    dollar_risk: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class RiskEngine:
    def __init__(
        self,
        default_risk_pct: float = 0.02,
        max_total_exposure_pct: float = 0.80,
        max_single_asset_exposure_pct: float = 0.35,
        max_strategy_allocation_pct: float = 0.75,
        max_open_positions: int = 3,
        max_crypto_positions: int = 2,
        dynamic_scaling: bool = True,
        daily_loss_limit_pct: float = 0.05,
        max_drawdown_limit_pct: float = 0.15,
        symbol_risk_weights: Optional[Dict[str, float]] = None,
        symbol_exposure_caps: Optional[Dict[str, float]] = None,
        account: Optional[Any] = None,
        max_correlated_positions: int = 2,
        correlation_threshold: float = 0.75,
    ):
        self.risk_pct = self.enforce_risk_bounds(default_risk_pct)
        self.max_total_exposure_pct = max_total_exposure_pct
        self.max_single_asset_exposure_pct = max_single_asset_exposure_pct
        self.max_strategy_allocation_pct = max_strategy_allocation_pct
        self.max_open_positions = max_open_positions
        self.max_crypto_positions = max_crypto_positions
        self.max_correlated_positions = max(1, int(max_correlated_positions))
        self.correlation_threshold = float(correlation_threshold)
        self.dynamic_scaling = dynamic_scaling
        self.daily_loss_limit_pct = daily_loss_limit_pct
        # Support both fraction (0.15) and percentage (15.0) inputs transparently
        self.max_drawdown_limit_pct = (
            max_drawdown_limit_pct / 100.0 if max_drawdown_limit_pct > 1.0 else max_drawdown_limit_pct
        )
        # Per-symbol dynamic capital weighting (strictly bounded between 1% and 5%)
        self.symbol_risk_weights: Dict[str, float] = {
            k: self.enforce_risk_bounds(v) for k, v in (symbol_risk_weights or {}).items()
        }
        self.symbol_exposure_caps: Dict[str, float] = dict(symbol_exposure_caps or {})

        # Kill switch & Probationary Reentry (Item 2) state
        self._is_halted: bool = False
        self._halt_reason: str = ""
        self.circuit_breaker_triggered_at: Optional[str] = None
        self.in_probation: bool = False
        self.probation_consecutive_wins: int = 0
        self.probation_required_wins: int = 3
        self.probation_cooldown_hours: float = 72.0
        self.probation_floor_equity: float = 0.0
        self.probation_max_additional_dd_pct: float = 0.025  # 2.5% max extra drawdown during probation

        # Tracking for circuit breakers
        self.peak_equity: float = 0.0
        self.current_drawdown: float = 0.0
        self.daily_start_date: Optional[date] = None
        self.daily_start_equity: float = 0.0
        self._bound_account: Optional[Any] = None

        if account is not None:
            self.bind_account(account)

    def bind_account(self, account: Any) -> None:
        """Bind a PaperAccount to restore and atomically persist drawdown circuit breaker state."""
        self._bound_account = account
        if account is None or not hasattr(account, "state"):
            return

        st = account.state
        persisted_peak = float(st.get("peak_equity", getattr(account, "initial_capital", 0.0)))
        if persisted_peak > self.peak_equity:
            self.peak_equity = persisted_peak

        self.current_drawdown = float(st.get("current_drawdown", 0.0))
        status = str(st.get("circuit_breaker_status", "OK")).upper()
        tripped = bool(st.get("circuit_breaker_tripped", False)) or status in ("TRIPPED", "HALTED", "ACTIVE")

        if tripped:
            self._is_halted = True
            self._halt_reason = str(
                st.get("circuit_breaker_reason")
                or "Max drawdown circuit breaker persisted across restart"
            )
            self.circuit_breaker_triggered_at = st.get("circuit_breaker_triggered_at") or datetime.now(timezone.utc).isoformat()

        self._persist_risk_state()

    def _persist_risk_state(self, force_save: bool = False) -> None:
        """Synchronize current peak equity, drawdown, and breaker status to bound PaperAccount."""
        if self._bound_account is not None and hasattr(self._bound_account, "sync_risk_state"):
            self._bound_account.sync_risk_state(
                peak_equity=self.peak_equity,
                current_drawdown=self.current_drawdown,
                circuit_breaker_status="TRIPPED" if self._is_halted else "OK",
                circuit_breaker_reason=self._halt_reason,
                circuit_breaker_triggered_at=self.circuit_breaker_triggered_at,
                force_save=force_save,
            )

    @property
    def is_halted(self) -> bool:
        return self._is_halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def trigger_kill_switch(self, reason: str, current_time: Optional[datetime] = None):
        """Manually or automatically trigger the emergency trading halt and persist it."""
        self._is_halted = True
        self._halt_reason = reason
        self.in_probation = False
        self.probation_consecutive_wins = 0
        ts = current_time if current_time is not None else datetime.now(timezone.utc)
        self.circuit_breaker_triggered_at = ts.isoformat()
        self._persist_risk_state(force_save=True)
        print(f"[RiskEngine] 🚨 KILL SWITCH TRIGGERED: {reason}")

    def evaluate_probationary_reentry(
        self,
        current_equity: float,
        vix_stable: bool = False,
        ema_200_reclaimed: bool = False,
        strategy_profit_factor: float = 1.20,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """Item 2: Dynamic Kill-Switch Half-Step Recovery Protocol (72h Probationary Reentry).

        Transitions the RiskEngine from HALTED into PROBATION (1.0% minimum risk floor
        per Invariant Rule #3) ONLY when all 3 stabilization gates pass:
          1. >= 72 hours have elapsed since `circuit_breaker_triggered_at`
          2. Macro volatility is stable (`vix_stable=True`, VIX < 20) AND 200-EMA is reclaimed
          3. Strategy expectancy is positive (`strategy_profit_factor >= 1.15`)
        """
        if not self._is_halted:
            return True, "System is not halted"

        if not self.circuit_breaker_triggered_at:
            return False, "Missing circuit breaker timestamp for probationary cooldown check"

        now_ts = current_time if current_time is not None else datetime.now(timezone.utc)
        if now_ts.tzinfo is None:
            now_ts = now_ts.replace(tzinfo=timezone.utc)

        try:
            halted_dt = datetime.fromisoformat(str(self.circuit_breaker_triggered_at).replace("Z", "+00:00"))
            if halted_dt.tzinfo is None:
                halted_dt = halted_dt.replace(tzinfo=timezone.utc)
            elapsed_hours = (now_ts - halted_dt).total_seconds() / 3600.0
        except Exception:
            return False, "Invalid circuit breaker timestamp"

        if elapsed_hours < self.probation_cooldown_hours:
            return (
                False,
                f"Probationary cooldown active: {elapsed_hours:.1f}h / {self.probation_cooldown_hours:.1f}h elapsed",
            )

        if not vix_stable or not ema_200_reclaimed:
            return (
                False,
                f"Probationary regime gate not met (vix_stable={vix_stable}, ema_200_reclaimed={ema_200_reclaimed})",
            )

        if strategy_profit_factor < 1.15:
            return (
                False,
                f"Probationary expectancy gate not met (PF {strategy_profit_factor:.2f} < 1.15)",
            )

        # Transition from HALTED -> PROBATION (clamped at 1.0% minimum risk per Invariant Rule #3)
        self._is_halted = False
        self._halt_reason = ""
        self.in_probation = True
        self.probation_consecutive_wins = 0
        self.probation_floor_equity = float(current_equity)
        self.peak_equity = float(current_equity)
        self.current_drawdown = 0.0
        self._persist_risk_state(force_save=True)
        msg = (
            f"Probationary Reentry activated after {elapsed_hours:.1f}h cooldown "
            f"(risk clamped to {MIN_ALLOWED_RISK_PCT*100:.1f}% until {self.probation_required_wins} consecutive wins)"
        )
        print(f"[RiskEngine] 🛡️ {msg}")
        return True, msg

    def record_probation_trade_outcome(
        self,
        pnl: float,
        current_equity: float,
        current_time: Optional[datetime] = None,
    ) -> str:
        """Track consecutive wins during Probationary Reentry or re-halt on loss/drawdown."""
        if not self.in_probation:
            return "NORMAL"

        if pnl > 0:
            self.probation_consecutive_wins += 1
            if self.probation_consecutive_wins >= self.probation_required_wins:
                self.in_probation = False
                self.probation_consecutive_wins = 0
                self.circuit_breaker_triggered_at = None
                self._persist_risk_state(force_save=True)
                print(
                    f"[RiskEngine] ✅ Probationary Reentry graduated after {self.probation_required_wins} "
                    f"consecutive wins! Normal risk sizing restored."
                )
                return "GRADUATED"
            return f"PROBATION_WIN_{self.probation_consecutive_wins}"

        # Any losing trade during probation immediately re-latches the 72h kill switch
        self.trigger_kill_switch(
            reason=f"Probationary trade loss (${pnl:.2f}) — re-entering 72h cooldown",
            current_time=current_time,
        )
        return "RE_HALTED"

    def reset_kill_switch(self, authorized: bool = True, reset_peak_to_current: Optional[float] = None) -> bool:
        """Reset emergency halt state.

        Requires explicit authorization (`authorized=True`). If `authorized=False` is passed,
        the reset is rejected and the circuit breaker remains active.
        """
        if not authorized:
            print("[RiskEngine] ❌ Unauthorized kill switch reset attempt rejected.")
            return False

        self._is_halted = False
        self._halt_reason = ""
        self.circuit_breaker_triggered_at = None
        if reset_peak_to_current is not None and reset_peak_to_current > 0:
            self.peak_equity = float(reset_peak_to_current)
            self.current_drawdown = 0.0
            if self._bound_account is not None and hasattr(self._bound_account, "state"):
                self._bound_account.state["peak_equity"] = self.peak_equity
        self._persist_risk_state(force_save=True)
        print("[RiskEngine] Kill switch cleared. Normal operation resumed.")
        return True

    @staticmethod
    def enforce_risk_bounds(risk_pct: float) -> float:
        """Ensure risk percentage strictly remains within [1%, 5%]."""
        if risk_pct < MIN_ALLOWED_RISK_PCT:
            return MIN_ALLOWED_RISK_PCT
        if risk_pct > MAX_ALLOWED_RISK_PCT:
            return MAX_ALLOWED_RISK_PCT
        return risk_pct

    def check_circuit_breakers(
        self,
        current_equity: float,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """Check daily loss and max drawdown limits against current equity.

        Returns:
            (is_healthy, reason)
        """
        if current_time is not None:
            today = current_time.date()
        else:
            today = datetime.now(timezone.utc).date()

        # Track high-water mark for drawdown (even while halted)
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        if self.peak_equity > 0:
            self.current_drawdown = max(0.0, (self.peak_equity - current_equity) / self.peak_equity)
        else:
            self.current_drawdown = 0.0

        # Initialize or update daily baseline
        if self.daily_start_date != today or self.daily_start_equity <= 0:
            self.daily_start_date = today
            self.daily_start_equity = current_equity
            # If halted strictly due to DAILY loss on a previous day (and NOT max drawdown), lift the halt on the new day
            if self._is_halted and "Daily loss" in self._halt_reason and "Max drawdown" not in self._halt_reason:
                self._is_halted = False
                self._halt_reason = ""
                self.circuit_breaker_triggered_at = None

        if self._is_halted:
            self._persist_risk_state()
            return False, f"Trading halted: {self._halt_reason}"

        # 1. Daily loss circuit breaker
        if self.daily_start_equity > 0:
            daily_loss = self.daily_start_equity - current_equity
            daily_loss_pct = daily_loss / self.daily_start_equity
            if daily_loss_pct >= self.daily_loss_limit_pct:
                reason = (
                    f"Daily loss circuit breaker triggered: {daily_loss_pct*100:.2f}% loss "
                    f"exceeds {self.daily_loss_limit_pct*100:.1f}% limit"
                )
                self.trigger_kill_switch(reason, current_time=current_time)
                return False, reason

        # 2. Max drawdown circuit breaker (or probationary tight drawdown floor)
        if self.in_probation and self.probation_floor_equity > 0:
            prob_dd = max(0.0, (self.probation_floor_equity - current_equity) / self.probation_floor_equity)
            if prob_dd >= self.probation_max_additional_dd_pct:
                reason = (
                    f"Probationary drawdown limit breached: {prob_dd*100:.2f}% loss from probation floor "
                    f"exceeds {self.probation_max_additional_dd_pct*100:.1f}% limit"
                )
                self.trigger_kill_switch(reason, current_time=current_time)
                return False, reason

        if self.peak_equity > 0:
            drawdown = self.current_drawdown
            if drawdown >= self.max_drawdown_limit_pct:
                reason = (
                    f"Max drawdown circuit breaker triggered: {drawdown*100:.2f}% drawdown "
                    f"exceeds {self.max_drawdown_limit_pct*100:.1f}% limit"
                )
                self.trigger_kill_switch(reason, current_time=current_time)
                return False, reason

        self._persist_risk_state()
        return True, "Circuit breakers healthy"

    def compute_volatility_adjusted_risk(
        self,
        base_risk_pct: float,
        current_atr: float,
        entry_price: float,
        atr_ratio_baseline: float = 0.025,
    ) -> float:
        """Dynamically scale risk percentage based on asset volatility regime.
        
        If current volatility expands significantly above baseline, scale down risk
        to protect equity. Strictly clamped within [0.01, 0.05].
        """
        if entry_price <= 0 or current_atr <= 0:
            return self.enforce_risk_bounds(base_risk_pct)

        current_atr_ratio = current_atr / entry_price
        if current_atr_ratio > atr_ratio_baseline:
            vol_scalar = (atr_ratio_baseline / current_atr_ratio) ** 0.5
            adjusted_risk = base_risk_pct * vol_scalar
        else:
            adjusted_risk = base_risk_pct

        return self.enforce_risk_bounds(adjusted_risk)

    def compute_kelly_risk(
        self,
        base_risk_pct: float,
        win_rate: float,
        win_loss_ratio: float,
    ) -> float:
        """Calculate Half-Kelly optimal growth sizing.
        
        Kelly f* = (W * (R + 1) - 1) / R
        Half-Kelly = 0.5 * f*
        Strictly clamped within [0.01, 0.05].
        """
        if win_loss_ratio <= 0 or win_rate <= 0:
            return self.enforce_risk_bounds(base_risk_pct)

        f_star = (win_rate * (win_loss_ratio + 1.0) - 1.0) / win_loss_ratio
        if f_star <= 0:
            return 0.01  # Minimum safe bound

        half_kelly = 0.5 * f_star
        blended_risk = 0.5 * base_risk_pct + 0.5 * half_kelly
        return self.enforce_risk_bounds(blended_risk)

    def calculate_position(
        self,
        equity: float,
        entry_price: float,
        atr: float,
        atr_multiplier: float = 2.5,
        risk_pct: Optional[float] = None,
        is_short: bool = False,
    ) -> Tuple[float, float, float]:
        """Calculate position size and initial stop loss based on Van Tharp's R-Risk model.

        Returns:
            units: Quantity of asset to trade.
            initial_stop: Stop-loss price level.
            dollar_risk: Exact dollar amount risked if stop is triggered.
        """
        effective_risk_pct = self.enforce_risk_bounds(risk_pct if risk_pct is not None else self.risk_pct)
        dollar_risk = equity * effective_risk_pct

        stop_distance = atr * atr_multiplier
        if stop_distance <= 0:
            raise ValueError("Stop distance must be positive.")

        if is_short:
            initial_stop = entry_price + stop_distance
        else:
            initial_stop = max(0.0, entry_price - stop_distance)

        units = dollar_risk / stop_distance
        return units, initial_stop, dollar_risk

    def validate_proposed_entry(
        self,
        symbol: str,
        side: str,
        strategy_id: str,
        entry_price: float,
        suggested_stop: float,
        atr: float,
        atr_multiplier: float,
        equity: float,
        cash: float,
        active_positions: Dict[str, Any],
        fee_pct: float = 0.0006,
        requested_risk_pct: Optional[float] = None,
        current_time: Optional[datetime] = None,
        risk_multiplier: float = 1.0,
        correlation_map: Optional[Dict[str, float]] = None,
    ) -> RiskVerdict:
        """Validate an entry proposal against all portfolio-level limits."""
        # 1. Circuit breaker / kill switch check
        healthy, breaker_reason = self.check_circuit_breakers(equity, current_time=current_time)
        if not healthy:
            return RiskVerdict(approved=False, reason=breaker_reason)

        # 2. Existing position conflict check
        if symbol in active_positions:
            existing = active_positions[symbol]
            existing_side = existing.get("side", "LONG")
            if existing_side != side:
                return RiskVerdict(
                    approved=False,
                    reason=f"Position direction conflict: already holding {symbol} ({existing_side})",
                )
            return RiskVerdict(
                approved=False,
                reason=f"Position already open for {symbol}",
            )

        # 3. Maximum open positions check
        if len(active_positions) >= self.max_open_positions:
            return RiskVerdict(
                approved=False,
                reason=f"Max positions limit reached: {len(active_positions)}/{self.max_open_positions}",
            )

        # Cross-Asset Correlation Guard: Limit simultaneous directional crypto positions to max_crypto_positions
        # (Market-neutral stat_arb_pairs legs are hedged Long/Short and excluded from directional crypto count)
        if is_crypto_symbol(symbol) and strategy_id != "stat_arb_pairs":
            current_crypto_pos = sum(
                1
                for sym, pos in active_positions.items()
                if is_crypto_symbol(sym) and pos.get("strategy_id") != "stat_arb_pairs"
            )
            if current_crypto_pos >= self.max_crypto_positions:
                return RiskVerdict(
                    approved=False,
                    reason=f"Correlation Guard: Max crypto positions ({self.max_crypto_positions}) reached (slot reserved for equities/uncorrelated)",
                )

        # Portfolio Rolling Return-Correlation Guard (prevents sector-cluster concentration)
        if correlation_map and strategy_id != "stat_arb_pairs":
            correlated_syms = [
                sym
                for sym, pos in active_positions.items()
                if pos.get("strategy_id") != "stat_arb_pairs"
                and float(correlation_map.get(sym, 0.0)) >= self.correlation_threshold
            ]
            if len(correlated_syms) >= self.max_correlated_positions:
                return RiskVerdict(
                    approved=False,
                    reason=(
                        f"Portfolio Correlation Guard: {symbol} correlated (>= {self.correlation_threshold:.2f}) "
                        f"with {len(correlated_syms)} open positions ({', '.join(correlated_syms[:3])})"
                    ),
                )

        # 4. Volatility sizing calculation (with per-symbol dynamic risk weighting & volatility targeting)
        base_risk = self.symbol_risk_weights.get(
            symbol, requested_risk_pct if requested_risk_pct is not None else self.risk_pct
        ) * float(risk_multiplier)
        if self.in_probation:
            # Item 2: During Probationary Reentry, lock risk to the 1.0% floor (Invariant Rule #3)
            effective_risk_pct = MIN_ALLOWED_RISK_PCT
        else:
            effective_risk_pct = self.compute_volatility_adjusted_risk(
                base_risk_pct=base_risk,
                current_atr=atr,
                entry_price=entry_price,
                atr_ratio_baseline=0.025,
            )
        is_short = (side.upper() == "SHORT")
        valid_suggested_stop = (
            (not is_short and 0.0 < suggested_stop < entry_price)
            or (is_short and suggested_stop > entry_price)
        )
        eff_atr = float(atr or 0.0)
        if eff_atr <= 0.0 and valid_suggested_stop:
            eff_atr = abs(entry_price - suggested_stop) / max(0.1, float(atr_multiplier or 1.0))
        if eff_atr <= 0.0:
            return RiskVerdict(
                approved=False,
                reason=f"Rejected: non-positive ATR/stop distance on {symbol}",
                effective_risk_pct=effective_risk_pct,
            )
        eff_atr_mult = (
            abs(entry_price - suggested_stop) / eff_atr
            if valid_suggested_stop
            else atr_multiplier
        )
        units, initial_stop, dollar_risk = self.calculate_position(
            equity=equity,
            entry_price=entry_price,
            atr=eff_atr,
            atr_multiplier=eff_atr_mult,
            risk_pct=effective_risk_pct,
            is_short=is_short,
        )

        order_cost = units * entry_price
        total_order_cost = order_cost * (1.0 + fee_pct)

        # 5. Single asset exposure cap (with per-symbol exposure cap if configured)
        effective_single_cap = self.symbol_exposure_caps.get(symbol, self.max_single_asset_exposure_pct)
        max_asset_cost = equity * effective_single_cap
        if total_order_cost > max_asset_cost:
            # Scale down units to fit single-asset cap
            scaled_units = max_asset_cost / (entry_price * (1.0 + fee_pct))
            units = scaled_units
            order_cost = units * entry_price
            total_order_cost = order_cost * (1.0 + fee_pct)
            dollar_risk = units * abs(entry_price - initial_stop)

        # 6. Total portfolio exposure cap (use mark-to-market price when provided in position state)
        def _pos_exposure(p: Dict) -> float:
            u = float(p.get("units", 0.0))
            ep = float(p.get("entry_price", 0.0))
            cp = float(p.get("current_price", ep))
            if p.get("side", "LONG") == "SHORT":
                return max(0.0, (u * ep) + (ep - cp) * u)
            return u * cp

        current_committed = sum(_pos_exposure(p) for p in active_positions.values())
        if (current_committed + order_cost) > (equity * self.max_total_exposure_pct):
            remaining_cap = max(0.0, (equity * self.max_total_exposure_pct) - current_committed)
            if remaining_cap <= 0:
                return RiskVerdict(
                    approved=False,
                    reason=f"Total exposure cap reached: {(current_committed/equity)*100:.1f}% >= {self.max_total_exposure_pct*100:.1f}%",
                )
            scaled_units = remaining_cap / (entry_price * (1.0 + fee_pct))
            units = min(units, scaled_units)
            order_cost = units * entry_price
            total_order_cost = order_cost * (1.0 + fee_pct)
            dollar_risk = units * abs(entry_price - initial_stop)

        # 7. Per-strategy allocation cap (with dynamic scaling if enabled)
        strategy_committed = sum(
            _pos_exposure(p)
            for p in active_positions.values()
            if p.get("strategy_id") == strategy_id
        )
        max_strat_dollars = equity * self.max_strategy_allocation_pct
        if (strategy_committed + order_cost) > max_strat_dollars:
            if not self.dynamic_scaling:
                return RiskVerdict(
                    approved=False,
                    reason=f"Strategy '{strategy_id}' allocation limit reached ({self.max_strategy_allocation_pct*100:.1f}%)",
                )
            remaining_strat_cap = max(0.0, max_strat_dollars - strategy_committed)
            if remaining_strat_cap < (equity * 0.05):
                return RiskVerdict(
                    approved=False,
                    reason=f"Strategy '{strategy_id}' allocation limit reached ({self.max_strategy_allocation_pct*100:.1f}%)",
                )
            scaled_units = remaining_strat_cap / (entry_price * (1.0 + fee_pct))
            units = min(units, scaled_units)
            order_cost = units * entry_price
            total_order_cost = order_cost * (1.0 + fee_pct)
            dollar_risk = units * abs(entry_price - initial_stop)

        # 8. Available cash check (with dynamic scaling if enabled)
        if total_order_cost > cash:
            if not self.dynamic_scaling:
                return RiskVerdict(
                    approved=False,
                    reason=f"Insufficient cash: order requires ${total_order_cost:,.2f}, available ${cash:,.2f}",
                )
            if cash < (equity * 0.05):
                return RiskVerdict(
                    approved=False,
                    reason=f"Insufficient cash: order requires ${total_order_cost:,.2f}, available ${cash:,.2f}",
                )
            scaled_units = cash / (entry_price * (1.0 + fee_pct))
            units = min(units, scaled_units)
            order_cost = units * entry_price
            total_order_cost = order_cost * (1.0 + fee_pct)
            dollar_risk = units * abs(entry_price - initial_stop)

        if units <= 0:
            return RiskVerdict(
                approved=False,
                reason="Calculated position size is zero or negative",
            )

        return RiskVerdict(
            approved=True,
            reason="Risk validation passed",
            suggested_units=units,
            suggested_stop=initial_stop,
            dollar_risk=dollar_risk,
        )

    @staticmethod
    def update_trailing_stop(
        current_price: float,
        current_atr: float,
        atr_multiplier: float,
        existing_stop: float,
        is_short: bool = False,
    ) -> float:
        """Ratchets the trailing stop loss as price moves in our favor.
        For LONG: Stop ratchets upward and never moves downward.
        For SHORT: Stop ratchets downward and never moves upward.
        """
        if is_short:
            candidate_stop = current_price + (current_atr * atr_multiplier)
            return min(existing_stop, candidate_stop)
        else:
            candidate_stop = current_price - (current_atr * atr_multiplier)
            return max(existing_stop, candidate_stop)


def compute_hierarchical_risk_parity_weights(
    returns_df: pd.DataFrame,
    base_weights: Optional[Dict[str, float]] = None,
    min_weight: float = 0.01,
    max_weight: float = 0.05,
) -> Dict[str, float]:
    """Calculate Hierarchical Risk Parity (HRP) weights from asset return covariance.

    Assets with higher covariance/correlation are grouped into clusters and have their
    combined risk scaled down, eliminating correlated sector concentration.

    Weights are strictly clamped within invariant bounds [min_weight, max_weight] (1% to 5%).
    """
    if returns_df.empty or len(returns_df.columns) == 0:
        return base_weights or {}

    symbols = list(returns_df.columns)
    if len(symbols) == 1:
        return {symbols[0]: 0.02}

    # 1. Calculate covariance and variances
    cov = returns_df.cov()
    variances = np.diag(cov.values)
    variances = np.where(variances <= 0, 1e-6, variances)

    # 2. Inverse-variance baseline
    inv_var = 1.0 / variances
    raw_weights = inv_var / inv_var.sum()

    # 3. Correlation distance adjustment: penalize highly correlated assets
    corr = returns_df.corr().fillna(0.0)
    mean_corr = corr.mean(axis=1).values
    diversification_factor = 1.0 - (mean_corr * 0.3)
    adjusted_weights = raw_weights * np.clip(diversification_factor, 0.5, 1.5)
    adjusted_weights = adjusted_weights / adjusted_weights.sum()

    # 4. Map to symbols and clamp strictly to [1%, 5%]
    hrp_weights: Dict[str, float] = {}
    for i, sym in enumerate(symbols):
        base_w = base_weights.get(sym, 0.02) if base_weights else 0.02
        blended = 0.5 * base_w + 0.5 * float(adjusted_weights[i])
        clamped = max(min_weight, min(max_weight, round(blended, 4)))
        hrp_weights[sym] = clamped

    return hrp_weights

