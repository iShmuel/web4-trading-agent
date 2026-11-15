"""Statistical Arbitrage & Market-Neutral Pairs Trading Strategy Engine.

Identifies cointegrated / highly correlated asset pairs, models their rolling hedge ratio (beta),
tracks spread mean-reversion via rolling Z-Score, checks dynamic stationarity (Ornstein-Uhlenbeck half-life),
and generates market-neutral Long/Short pairs signals across multiple dynamic assets.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple, List
from itertools import combinations
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
    is_stationary: bool = True
    correlation: float = 1.0
    half_life: float = 24.0


class StatArbPairsStrategy:
    """Market-Neutral Statistical Arbitrage & Dynamic Multi-Asset Pairs Engine."""

    STRATEGY_ID = "stat_arb_pairs"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.lookback_period = int(cfg.get("stat_arb_lookback", 48))
        self.entry_z_score = float(cfg.get("stat_arb_entry_z", 2.0))
        self.exit_z_score = float(cfg.get("stat_arb_exit_z", 0.5))
        self.stop_z_score = float(cfg.get("stat_arb_stop_z", 3.5))
        self.min_correlation = float(cfg.get("min_pair_correlation", 0.70))
        self.max_half_life = float(cfg.get("max_pair_half_life_hours", 72.0))
        # Cost-aware trade acceptance gate (0.0 = disabled; preserves historical behaviour).
        # A pair entry is only accepted when expected spread reversion >= ratio x round-trip friction.
        self.min_edge_cost_ratio = float(cfg.get("stat_arb_min_edge_cost_ratio", 0.0))
        self.cost_per_side_pct = float(
            cfg.get(
                "stat_arb_cost_per_side_pct",
                float(cfg.get("fee_pct", 0.0006)) + float(cfg.get("pair_slippage_pct", cfg.get("slippage_pct", 0.0004))),
            )
        )

    def estimate_expected_edge_vs_cost(
        self,
        series_a: pd.Series,
        series_b: pd.Series,
        beta: float,
        z_score: float,
    ) -> Tuple[float, float, float]:
        """Deterministically estimate the economics of entering a pair trade now.

        Returns (expected_gross_edge_usd_per_a_unit, round_trip_cost_usd_per_a_unit, ratio).

        Per 1 unit of A hedged with ``beta`` units of B, the position profits by the spread
        move.  Reverting from |z| to ``exit_z_score`` captures (|z| - exit_z) * sigma_spread.
        Round-trip friction applies to BOTH legs on BOTH sides:
            2 sides x cost_per_side x (price_a + beta * price_b).
        """
        sub_a = series_a.iloc[-self.lookback_period:]
        sub_b = series_b.iloc[-self.lookback_period:]
        spread = sub_a - beta * sub_b
        sigma = float(spread.std())
        pa = float(sub_a.iloc[-1])
        pb = float(sub_b.iloc[-1])
        expected_edge = max(0.0, abs(z_score) - self.exit_z_score) * sigma
        round_trip_cost = 2.0 * self.cost_per_side_pct * (pa + beta * pb)
        ratio = expected_edge / round_trip_cost if round_trip_cost > 0 else 0.0
        return expected_edge, round_trip_cost, ratio

    def calculate_spread_and_zscore(
        self,
        series_a: pd.Series,
        series_b: pd.Series,
    ) -> Tuple[float, float, float]:
        """Calculate hedge ratio (beta), current spread, and rolling Z-Score."""
        if len(series_a) < self.lookback_period or len(series_b) < self.lookback_period:
            return 0.0, 1.0, 0.0

        sub_a = series_a.iloc[-self.lookback_period:]
        sub_b = series_b.iloc[-self.lookback_period:]

        # Rolling hedge ratio (Beta = Cov(A, B) / Var(B))
        cov = np.cov(sub_a, sub_b)[0, 1]
        var_b = np.var(sub_b)
        beta = float(cov / var_b) if var_b > 0 else 1.0
        beta = max(0.01, min(beta, 10.0))

        # Spread series
        spread_series = sub_a - (beta * sub_b)
        mean_spread = float(spread_series.mean())
        std_spread = float(spread_series.std())

        cur_spread = float(spread_series.iloc[-1])
        z_score = float((cur_spread - mean_spread) / std_spread) if std_spread > 0 else 0.0

        return z_score, beta, cur_spread

    def calculate_cointegration_quality(
        self,
        series_a: pd.Series,
        series_b: pd.Series,
        beta: float,
    ) -> Tuple[float, float, bool]:
        """Calculates Pearson correlation, Ornstein-Uhlenbeck mean-reverting half-life, and stationarity.

        Returns:
            (correlation, half_life_hours, is_stationary)
        """
        if len(series_a) < self.lookback_period or len(series_b) < self.lookback_period:
            return 0.0, 999.0, False

        sub_a = series_a.iloc[-self.lookback_period:]
        sub_b = series_b.iloc[-self.lookback_period:]

        corr = float(np.corrcoef(sub_a, sub_b)[0, 1])
        if np.isnan(corr):
            corr = 0.0

        spread = sub_a - (beta * sub_b)
        d_spread = spread.diff().dropna()
        prev_spread = (spread - spread.mean()).iloc[:-1]

        var_prev = np.var(prev_spread)
        if var_prev > 0 and len(d_spread) > 5:
            lam = float(np.cov(d_spread, prev_spread)[0, 1] / var_prev)
        else:
            lam = 0.0

        # Mean reverting if lambda < -0.01 (drift pulls spread back toward mean)
        if lam < -0.01:
            half_life = float(-np.log(2) / lam)
        else:
            half_life = 999.0

        is_stationary = (corr >= self.min_correlation) and (lam < -0.01) and (0.1 <= half_life <= self.max_half_life)
        return corr, round(half_life, 1), is_stationary

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
                is_stationary=False,
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
                is_stationary=False,
            )

        z_score, beta, cur_spread = self.calculate_spread_and_zscore(combined.iloc[:, 0], combined.iloc[:, 1])
        corr, half_life, is_stat = self.calculate_cointegration_quality(combined.iloc[:, 0], combined.iloc[:, 1], beta)

        # If holding a pair position, check exit conditions
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
                    is_stationary=is_stat,
                    correlation=corr,
                    half_life=half_life,
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
                    is_stationary=is_stat,
                    correlation=corr,
                    half_life=half_life,
                )
            elif corr < 0.40:
                return PairTradeSignal(
                    pair_id=pair_id,
                    asset_a=asset_a,
                    asset_b=asset_b,
                    timestamp=now,
                    action="CLOSE_PAIR",
                    z_score=z_score,
                    hedge_ratio=beta,
                    spread=cur_spread,
                    reason=f"Regime breakdown: Correlation dropped to {corr:.2f} (< 0.40)",
                    confidence=0.90,
                    is_stationary=False,
                    correlation=corr,
                    half_life=half_life,
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
                    reason=f"Holding pair position: current Z-Score {z_score:+.2f} (Half-life {half_life:.1f}h)",
                    is_stationary=is_stat,
                    correlation=corr,
                    half_life=half_life,
                )

        # No existing pair position: check dynamic stationarity gate
        if not is_stat:
            return PairTradeSignal(
                pair_id=pair_id,
                asset_a=asset_a,
                asset_b=asset_b,
                timestamp=now,
                action="NO_TRADE",
                z_score=z_score,
                hedge_ratio=beta,
                spread=cur_spread,
                reason=f"Filtered by Stationarity Gate: Corr={corr:.2f} (min {self.min_correlation}), Half-life={half_life:.1f}h (max {self.max_half_life}h)",
                is_stationary=False,
                correlation=corr,
                half_life=half_life,
            )

        # Never enter when spread divergence is already at or beyond the stop-loss Z-score
        # (prevents immediate next-tick stop-out churn if |Z| >= stop_z_score).
        if abs(z_score) >= self.stop_z_score:
            return PairTradeSignal(
                pair_id=pair_id,
                asset_a=asset_a,
                asset_b=asset_b,
                timestamp=now,
                action="NO_TRADE",
                z_score=z_score,
                hedge_ratio=beta,
                spread=cur_spread,
                reason=(
                    f"Blocked by Stop-Z Ceiling: Spread Z-Score {z_score:+.2f} "
                    f"exceeds stop threshold ±{self.stop_z_score}"
                ),
                is_stationary=is_stat,
                correlation=corr,
                half_life=half_life,
            )

        # Cost-aware acceptance gate: reject entries whose expected reversion cannot cover friction
        if self.min_edge_cost_ratio > 0.0 and abs(z_score) >= self.entry_z_score:
            _, _, edge_ratio = self.estimate_expected_edge_vs_cost(
                combined.iloc[:, 0], combined.iloc[:, 1], beta, z_score
            )
            if edge_ratio < self.min_edge_cost_ratio:
                return PairTradeSignal(
                    pair_id=pair_id,
                    asset_a=asset_a,
                    asset_b=asset_b,
                    timestamp=now,
                    action="NO_TRADE",
                    z_score=z_score,
                    hedge_ratio=beta,
                    spread=cur_spread,
                    reason=(
                        f"Blocked by Cost Gate: expected reversion/round-trip cost = {edge_ratio:.2f} "
                        f"< required {self.min_edge_cost_ratio:.2f}"
                    ),
                    is_stationary=is_stat,
                    correlation=corr,
                    half_life=half_life,
                )

        # Check entry triggers
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
                reason=f"Spread underpriced: Z-Score {z_score:+.2f} <= -{self.entry_z_score} (Long {asset_a}, Short {asset_b} [Beta={beta:.2f}, HL={half_life:.1f}h])",
                confidence=min(1.0, abs(z_score) / 3.0),
                is_stationary=True,
                correlation=corr,
                half_life=half_life,
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
                reason=f"Spread overpriced: Z-Score {z_score:+.2f} >= +{self.entry_z_score} (Short {asset_a}, Long {asset_b} [Beta={beta:.2f}, HL={half_life:.1f}h])",
                confidence=min(1.0, abs(z_score) / 3.0),
                is_stationary=True,
                correlation=corr,
                half_life=half_life,
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
            is_stationary=is_stat,
            correlation=corr,
            half_life=half_life,
        )

    def scan_dynamic_pairs(
        self,
        market_data: Dict[str, pd.DataFrame],
        candidate_universe: Optional[List[Tuple[str, str]]] = None,
        active_pair_positions: Optional[Dict[str, str]] = None,
    ) -> List[PairTradeSignal]:
        """Dynamically scans candidate pairs across market data, filters non-stationary pairs, and returns ranked signals."""
        signals: List[PairTradeSignal] = []
        active_pos = active_pair_positions or {}

        # If candidate universe is not provided, discover pairs from available market data keys
        if candidate_universe is None:
            symbols = list(market_data.keys())
            candidate_universe = list(combinations(symbols, 2))

        for asset_a, asset_b in candidate_universe:
            if asset_a not in market_data or asset_b not in market_data:
                continue

            pair_id = f"{asset_a}:{asset_b}"
            existing = active_pos.get(pair_id)

            sig = self.evaluate_pair(
                asset_a=asset_a,
                asset_b=asset_b,
                df_a=market_data[asset_a],
                df_b=market_data[asset_b],
                existing_pair_position=existing,
            )
            signals.append(sig)

        # Sort: CLOSE_PAIR first, then highest confidence actionable entries, then others
        def sort_key(s: PairTradeSignal) -> int:
            if s.action == "CLOSE_PAIR":
                return 0
            elif s.action in ("LONG_A_SHORT_B", "SHORT_A_LONG_B"):
                return 1
            elif s.action == "HOLD":
                return 2
            return 3

        signals.sort(key=sort_key)
        return signals



def discover_cointegrated_pairs(
    symbols: List[str],
    config: Optional[Dict[str, Any]] = None,
    market_data: Optional[Dict[str, pd.DataFrame]] = None,
    max_crypto_pairs: int = 3,
    max_stock_pairs: int = 3,
    max_pairs_per_symbol: int = 2,
) -> List[List[str]]:
    """Dynamically discover stationary, cointegrated pairs from the current active watchlist.

    Pairs are formed within the same asset class (crypto-crypto and stock-stock) so
    trading hours and volatility regimes match. Candidates are filtered by the live
    ``StatArbPairsStrategy`` stationarity gate (correlation >= min_pair_correlation and
    Ornstein-Uhlenbeck half-life <= max_pair_half_life_hours) and ranked by
    ``correlation / log2(2 + half_life)``.
    """
    from core.data_loader import fetch_ohlcv, is_crypto_symbol

    cfg = config or {}
    strat = StatArbPairsStrategy(cfg)
    timeframe = cfg.get("timeframe", "1h")

    data: Dict[str, pd.DataFrame] = dict(market_data) if market_data else {}
    for sym in symbols:
        if sym in data:
            continue
        try:
            df = fetch_ohlcv(sym, timeframe=timeframe, days=30 if is_crypto_symbol(sym) else 90)
            if df is not None and len(df) >= strat.lookback_period:
                data[sym] = df
        except Exception:
            continue

    cryptos = [s for s in symbols if is_crypto_symbol(s) and s in data]
    stocks = [s for s in symbols if not is_crypto_symbol(s) and s in data]

    def _select_top(group: List[str], limit: int) -> List[List[str]]:
        scored = []
        for a, b in combinations(group, 2):
            sig = strat.evaluate_pair(a, b, data[a], data[b])
            if sig.is_stationary:
                score = sig.correlation / np.log2(2.0 + max(0.5, sig.half_life))
                scored.append((score, a, b))
        scored.sort(key=lambda x: x[0], reverse=True)
        chosen: List[List[str]] = []
        counts: Dict[str, int] = {}
        for _, a, b in scored:
            if len(chosen) >= limit:
                break
            if counts.get(a, 0) >= max_pairs_per_symbol or counts.get(b, 0) >= max_pairs_per_symbol:
                continue
            chosen.append([a, b])
            counts[a] = counts.get(a, 0) + 1
            counts[b] = counts.get(b, 0) + 1
        return chosen

    selected = _select_top(cryptos, max_crypto_pairs) + _select_top(stocks, max_stock_pairs)
    return selected
