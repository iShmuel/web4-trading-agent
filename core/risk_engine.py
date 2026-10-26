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
    ):
        self.risk_pct = self.enforce_risk_bounds(default_risk_pct)
        self.max_total_exposure_pct = max_total_exposure_pct
        self.max_single_asset_exposure_pct = max_single_asset_exposure_pct
        self.max_strategy_allocation_pct = max_strategy_allocation_pct
        self.max_open_positions = max_open_positions
        self.max_crypto_positions = max_crypto_positions
        self.dynamic_scaling = dynamic_scaling
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.max_drawdown_limit_pct = max_drawdown_limit_pct
        # Per-symbol dynamic capital weighting (strictly bounded between 1% and 5%)
        self.symbol_risk_weights: Dict[str, float] = {
            k: self.enforce_risk_bounds(v) for k, v in (symbol_risk_weights or {}).items()
        }
        self.symbol_exposure_caps: Dict[str, float] = dict(symbol_exposure_caps or {})

        # Kill switch state
        self._is_halted: bool = False
        self._halt_reason: str = ""

        # Tracking for circuit breakers
        self.peak_equity: float = 0.0
        self.daily_start_date: Optional[date] = None
        self.daily_start_equity: float = 0.0

    @property
    def is_halted(self) -> bool:
        return self._is_halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def trigger_kill_switch(self, reason: str):
        """Manually or automatically trigger the emergency trading halt."""
        self._is_halted = True
        self._halt_reason = reason
        print(f"[RiskEngine] 🚨 KILL SWITCH TRIGGERED: {reason}")

    def reset_kill_switch(self):
        """Reset emergency halt state."""
        self._is_halted = False
        self._halt_reason = ""
        print("[RiskEngine] Kill switch cleared. Normal operation resumed.")

    @staticmethod
    def enforce_risk_bounds(risk_pct: float) -> float:
        """Ensure risk percentage strictly remains within [1%, 5%]."""
        if risk_pct < MIN_ALLOWED_RISK_PCT:
            return MIN_ALLOWED_RISK_PCT
        if risk_pct > MAX_ALLOWED_RISK_PCT:
            return MAX_ALLOWED_RISK_PCT
        return risk_pct

    def check_circuit_breakers(self, current_equity: float) -> Tuple[bool, str]:
        """Check daily loss and max drawdown limits against current equity.

        Returns:
            (is_healthy, reason)
        """
        if self._is_halted:
            return False, f"Trading halted: {self._halt_reason}"

        today = datetime.now(timezone.utc).date()

        # Initialize or update daily baseline
        if self.daily_start_date != today or self.daily_start_equity <= 0:
            self.daily_start_date = today
            self.daily_start_equity = current_equity

        # Track high-water mark for drawdown
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        # 1. Daily loss circuit breaker
        if self.daily_start_equity > 0:
            daily_loss = self.daily_start_equity - current_equity
            daily_loss_pct = daily_loss / self.daily_start_equity
            if daily_loss_pct >= self.daily_loss_limit_pct:
                reason = (
                    f"Daily loss circuit breaker triggered: {daily_loss_pct*100:.2f}% loss "
                    f"exceeds {self.daily_loss_limit_pct*100:.1f}% limit"
                )
                self.trigger_kill_switch(reason)
                return False, reason

        # 2. Max drawdown circuit breaker
        if self.peak_equity > 0:
            drawdown = (self.peak_equity - current_equity) / self.peak_equity
            if drawdown >= self.max_drawdown_limit_pct:
                reason = (
                    f"Max drawdown circuit breaker triggered: {drawdown*100:.2f}% drawdown "
                    f"exceeds {self.max_drawdown_limit_pct*100:.1f}% limit"
                )
                self.trigger_kill_switch(reason)
                return False, reason

        return True, "Circuit breakers healthy"

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
    ) -> RiskVerdict:
        """Validate an entry proposal against all portfolio-level limits."""
        # 1. Circuit breaker / kill switch check
        healthy, breaker_reason = self.check_circuit_breakers(equity)
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

        # Cross-Asset Correlation Guard: Limit simultaneous crypto positions to max_crypto_positions
        if is_crypto_symbol(symbol):
            current_crypto_pos = sum(1 for sym in active_positions if is_crypto_symbol(sym))
            if current_crypto_pos >= self.max_crypto_positions:
                return RiskVerdict(
                    approved=False,
                    reason=f"Correlation Guard: Max crypto positions ({self.max_crypto_positions}) reached (slot reserved for equities/uncorrelated)",
                )

        # 4. Volatility sizing calculation (with per-symbol dynamic risk weighting within 1%-5%)
        effective_risk_pct = self.symbol_risk_weights.get(
            symbol, requested_risk_pct if requested_risk_pct is not None else self.risk_pct
        )
        is_short = (side.upper() == "SHORT")
        units, initial_stop, dollar_risk = self.calculate_position(
            equity=equity,
            entry_price=entry_price,
            atr=atr,
            atr_multiplier=atr_multiplier,
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

        # 6. Total portfolio exposure cap
        current_committed = sum(p["units"] * p["entry_price"] for p in active_positions.values())
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
            p["units"] * p["entry_price"]
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
