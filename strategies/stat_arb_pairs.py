"""Statistical Arbitrage & Market-Neutral Pairs Trading Strategy Engine.

Identifies cointegrated / highly correlated asset pairs, models their rolling hedge ratio (beta),
tracks spread mean-reversion via rolling Z-Score, and generates market-neutral Long/Short pairs signals.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
import numpy as np
import pandas as pd


@dataclass
class PairTradeSignal:
    pair_id: str
    asset_a: str
    asset_b: str
    timestamp: datetime
    action: str  # 'LONG_A_SHORT_B', 'SHORT_A_LONG_B', 'CLOSE_PAIR', 'HOLD', 'NO_TRADE'
    z_score: float
    hedge_ratio: float
    spread: float
    reason: str
    confidence: float = 0.5


class StatArbPairsStrategy:
    """Market-Neutral Statistical Arbitrage Pairs Engine."""

    STRATEGY_ID = "stat_arb_pairs"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.lookback_period = cfg.get("stat_arb_lookback", 24)
        self.entry_z_score = cfg.get("stat_arb_entry_z", 2.0)
        self.exit_z_score = cfg.get("stat_arb_exit_z", 0.5)
        self.stop_z_score = cfg.get("stat_arb_stop_z", 3.5)

    def calculate_spread_and_zscore(
        self,
        series_a: pd.Series,
        series_b: pd.Series,
    ) -> Tuple[float, float, float]:
        """Calculate hedge ratio (beta), current spread, and rolling Z-Score.

        Returns:
            (z_score, hedge_ratio, current_spread)
        """
        if len(series_a) < self.lookback_period or len(series_b) < self.lookback_period:
            return 0.0, 1.0, 0.0

        sub_a = series_a.iloc[-self.lookback_period:]
        sub_b = series_b.iloc[-self.lookback_period:]

        # Rolling hedge ratio (Beta = Cov(A, B) / Var(B))
        cov = np.cov(sub_a, sub_b)[0, 1]
        var_b = np.var(sub_b)
        beta = float(cov / var_b) if var_b > 0 else 1.0
        beta = max(0.01, beta)

        # Spread series
        spread_series = sub_a - (beta * sub_b)
        mean_spread = float(spread_series.mean())
        std_spread = float(spread_series.std())

        cur_spread = float(spread_series.iloc[-1])
        z_score = float((cur_spread - mean_spread) / std_spread) if std_spread > 0 else 0.0

        return z_score, beta, cur_spread

    def evaluate_pair(
        self,
        asset_a: str,
        asset_b: str,
        df_a: pd.DataFrame,
        df_b: pd.DataFrame,
        existing_pair_position: Optional[str] = None,
    ) -> PairTradeSignal:
        """Evaluate a pair of assets and return a PairTradeSignal."""
        now = datetime.now(timezone.utc)
        pair_id = f"{asset_a}:{asset_b}"

        if df_a is None or df_b is None or len(df_a) < self.lookback_period or len(df_b) < self.lookback_period:
            return PairTradeSignal(
                pair_id=pair_id,
                asset_a=asset_a,
                asset_b=asset_b,
                timestamp=now,
                action="NO_TRADE",
                z_score=0.0,
                hedge_ratio=1.0,
                spread=0.0,
                reason="Insufficient historical bars for pair",
            )

        # Align closes on common timestamps
        combined = pd.concat([df_a["close"], df_b["close"]], axis=1, join="inner").dropna()
        if len(combined) < self.lookback_period:
            return PairTradeSignal(
                pair_id=pair_id,
                asset_a=asset_a,
                asset_b=asset_b,
                timestamp=now,
                action="NO_TRADE",
                z_score=0.0,
                hedge_ratio=1.0,
                spread=0.0,
                reason="Insufficient synchronized timestamps between pair assets",
            )

        z_score, beta, cur_spread = self.calculate_spread_and_zscore(combined.iloc[:, 0], combined.iloc[:, 1])

        # If holding a pair position, check exit
        if existing_pair_position is not None:
            if abs(z_score) <= self.exit_z_score:
                return PairTradeSignal(
                    pair_id=pair_id,
                    asset_a=asset_a,
                    asset_b=asset_b,
                    timestamp=now,
                    action="CLOSE_PAIR",
                    z_score=z_score,
                    hedge_ratio=beta,
                    spread=cur_spread,
                    reason=f"Spread mean-reverted: Z-Score {z_score:+.2f} reached target (|Z| <= {self.exit_z_score})",
                    confidence=0.90,
                )
            elif abs(z_score) >= self.stop_z_score:
                return PairTradeSignal(
                    pair_id=pair_id,
                    asset_a=asset_a,
                    asset_b=asset_b,
                    timestamp=now,
                    action="CLOSE_PAIR",
                    z_score=z_score,
                    hedge_ratio=beta,
                    spread=cur_spread,
                    reason=f"Stop loss triggered: Spread divergence Z-Score {z_score:+.2f} exceeded stop {self.stop_z_score}",
                    confidence=0.95,
                )
            else:
                return PairTradeSignal(
                    pair_id=pair_id,
                    asset_a=asset_a,
                    asset_b=asset_b,
                    timestamp=now,
                    action="HOLD",
                    z_score=z_score,
                    hedge_ratio=beta,
                    spread=cur_spread,
                    reason=f"Holding pair position: current Z-Score {z_score:+.2f}",
                )

        # No existing pair position: check entry
        if z_score <= -self.entry_z_score:
            return PairTradeSignal(
                pair_id=pair_id,
                asset_a=asset_a,
                asset_b=asset_b,
                timestamp=now,
                action="LONG_A_SHORT_B",
                z_score=z_score,
                hedge_ratio=beta,
                spread=cur_spread,
                reason=f"Spread underpriced: Z-Score {z_score:+.2f} <= -{self.entry_z_score} (Long {asset_a}, Short {asset_b})",
                confidence=min(1.0, abs(z_score) / 3.0),
            )
        elif z_score >= self.entry_z_score:
            return PairTradeSignal(
                pair_id=pair_id,
                asset_a=asset_a,
                asset_b=asset_b,
                timestamp=now,
                action="SHORT_A_LONG_B",
                z_score=z_score,
                hedge_ratio=beta,
                spread=cur_spread,
                reason=f"Spread overpriced: Z-Score {z_score:+.2f} >= +{self.entry_z_score} (Short {asset_a}, Long {asset_b})",
                confidence=min(1.0, abs(z_score) / 3.0),
            )

        return PairTradeSignal(
            pair_id=pair_id,
            asset_a=asset_a,
            asset_b=asset_b,
            timestamp=now,
            action="NO_TRADE",
            z_score=z_score,
            hedge_ratio=beta,
            spread=cur_spread,
            reason=f"Spread inside normal band: Z-Score {z_score:+.2f}",
        )
