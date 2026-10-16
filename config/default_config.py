"""Default strategy constants and hard-coded risk guardrails.

CRITICAL: The risk engine enforces that account_risk_pct NEVER exceeds
MAX_RISK_CAP (0.05 / 5%) under any circumstances.
"""

from dataclasses import dataclass

# --- HARD RISK BOUNDS (IMMUTABLE FOR THE AGENT) ---
MIN_RISK_CAP: float = 0.01  # Minimum 1% risk per trade
MAX_RISK_CAP: float = 0.05  # Maximum 5% risk per trade
MAX_DAILY_DRAWDOWN: float = 0.06  # 6% daily account circuit breaker
MAX_CONCURRENT_POSITIONS: int = 3  # Maximum concurrent open trades


@dataclass
class StrategyConfig:
    # Asset & Timeframe
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"

    # Strategy Parameters (Can be tuned by the self-improvement loop)
    breakout_lookback: int = 24       # 24 candles high for breakout
    ema_trend_filter: int = 200       # 200 EMA trend filter
    atr_period: int = 14              # 14-period ATR
    atr_multiplier: float = 2.5       # Trailing stop distance in ATRs

    # Risk Parameters (Must be between MIN_RISK_CAP and MAX_RISK_CAP)
    account_risk_pct: float = 0.02    # Default 2% risk per trade

    # Exchange frictions
    fee_pct: float = 0.0006           # 0.06% taker fee per trade
    slippage_pct: float = 0.0004      # 0.04% estimated slippage
