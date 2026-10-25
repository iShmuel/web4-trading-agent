"""Standard signal contract for all strategy engines.

Every strategy must produce TradingSignal objects through this interface.
Signals are proposals — they do not authorize execution on their own.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any


class SignalDirection(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"  # explicit no-trade


class SignalStatus(Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    PARTIAL_EXIT = "PARTIAL_EXIT"
    NO_TRADE = "NO_TRADE"
    HOLD = "HOLD"


@dataclass
class TradingSignal:
    """Standard signal object produced by strategy engines."""

    # Identity
    strategy_id: str
    symbol: str
    timestamp: datetime

    # Signal
    direction: SignalDirection
    status: SignalStatus

    # Price context
    reference_price: float
    atr: float = 0.0

    # Signal quality
    signal_strength: float = 0.0  # 0.0 to 1.0

    # Entry parameters (populated for ENTRY signals)
    suggested_stop: float = 0.0
    suggested_take_profit: float = 0.0
    take_profit_ratio: float = 0.0  # fraction to scale out

    # Cost estimates
    estimated_fee_pct: float = 0.0006
    estimated_slippage_pct: float = 0.0004

    # Exit parameters (populated for EXIT signals)
    exit_reason: str = ""

    # Indicator snapshot for traceability
    indicators: Dict[str, float] = field(default_factory=dict)

    # No-trade explanation
    no_trade_reason: str = ""

    # Configuration version that generated this signal
    config_version: str = ""

    @property
    def is_actionable(self) -> bool:
        """Whether this signal proposes an action (entry, exit, or partial scale-out)."""
        return self.status in (SignalStatus.ENTRY, SignalStatus.EXIT, SignalStatus.PARTIAL_EXIT)

    @property
    def estimated_cost_pct(self) -> float:
        """Total estimated round-trip cost as percentage."""
        return (self.estimated_fee_pct + self.estimated_slippage_pct) * 2

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "direction": self.direction.value,
            "status": self.status.value,
            "reference_price": self.reference_price,
            "atr": self.atr,
            "signal_strength": self.signal_strength,
            "suggested_stop": self.suggested_stop,
            "suggested_take_profit": self.suggested_take_profit,
            "estimated_fee_pct": self.estimated_fee_pct,
            "estimated_slippage_pct": self.estimated_slippage_pct,
            "exit_reason": self.exit_reason,
            "no_trade_reason": self.no_trade_reason,
            "indicators": self.indicators,
        }


def create_no_trade_signal(
    strategy_id: str,
    symbol: str,
    price: float,
    reason: str,
    indicators: Optional[Dict[str, Any]] = None,
) -> TradingSignal:
    """Helper to create a no-trade signal with explanation and indicators."""
    return TradingSignal(
        strategy_id=strategy_id,
        symbol=symbol,
        timestamp=datetime.now(timezone.utc),
        direction=SignalDirection.FLAT,
        status=SignalStatus.NO_TRADE,
        reference_price=price,
        no_trade_reason=reason,
        indicators=indicators or {},
    )
