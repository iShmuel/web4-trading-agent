"""Risk engine module enforcing strict 1%-5% account risk bounds and ATR trailing stops."""

from typing import Tuple

# Hard bounds: The AI self-improvement loop CANNOT breach these limits.
MIN_ALLOWED_RISK_PCT = 0.01  # 1%
MAX_ALLOWED_RISK_PCT = 0.05  # 5%


class RiskEngine:
    def __init__(self, default_risk_pct: float = 0.02):
        self.risk_pct = self.enforce_risk_bounds(default_risk_pct)

    @staticmethod
    def enforce_risk_bounds(risk_pct: float) -> float:
        """Ensure risk percentage strictly remains within [1%, 5%]."""
        if risk_pct < MIN_ALLOWED_RISK_PCT:
            print(f"[RiskEngine] Warning: Requested risk {risk_pct*100:.1f}% below minimum 1%. Clamping to 1%.")
            return MIN_ALLOWED_RISK_PCT
        if risk_pct > MAX_ALLOWED_RISK_PCT:
            print(f"[RiskEngine] Warning: Requested risk {risk_pct*100:.1f}% exceeds maximum 5%. Clamping to 5%.")
            return MAX_ALLOWED_RISK_PCT
        return risk_pct

    def calculate_position(
        self,
        equity: float,
        entry_price: float,
        atr: float,
        atr_multiplier: float = 2.5,
        risk_pct: float = None,
        is_short: bool = False,
    ) -> Tuple[float, float, float]:
        """
        Calculate position size and initial stop loss based on Van Tharp's R-Risk model.

        Returns:
            units: Quantity of asset to trade.
            initial_stop: Stop-loss price level.
            dollar_risk: Exact dollar amount risked if stop is triggered.
        """
        effective_risk_pct = self.enforce_risk_bounds(risk_pct if risk_pct is not None else self.risk_pct)

        # Dollar amount allowed to lose on this trade (strictly 1% to 5% of equity)
        dollar_risk = equity * effective_risk_pct

        # Stop distance is volatility-adjusted (ATR * multiplier)
        stop_distance = atr * atr_multiplier
        if stop_distance <= 0:
            raise ValueError("Stop distance must be positive.")

        if is_short:
            initial_stop = entry_price + stop_distance
        else:
            initial_stop = max(0.0, entry_price - stop_distance)

        # Units = Dollar Risk / Stop Distance
        units = dollar_risk / stop_distance

        return units, initial_stop, dollar_risk

    @staticmethod
    def update_trailing_stop(
        current_price: float,
        current_atr: float,
        atr_multiplier: float,
        existing_stop: float,
        is_short: bool = False,
    ) -> float:
        """
        Ratchets the trailing stop loss as price moves in our favor.
        For LONG: Stop ratchets upward and never moves downward.
        For SHORT: Stop ratchets downward and never moves upward.
        """
        if is_short:
            candidate_stop = current_price + (current_atr * atr_multiplier)
            return min(existing_stop, candidate_stop)
        else:
            candidate_stop = current_price - (current_atr * atr_multiplier)
            return max(existing_stop, candidate_stop)
