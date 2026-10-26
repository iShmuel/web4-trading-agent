"""Rigorous backtest matrix evaluating parallel trading improvements:
1. Baseline: Current setup (75% strat cap, max 3 positions, dict scan order, reject if exceeds)
2. Quality_Ranking: Prioritize candidate symbols by quality score (volume ratio * trend strength) before risk evaluation
3. Dynamic_Scaling: Scale down position units to fit remaining strategy/portfolio capacity (>=5% equity) instead of rejecting
4. Correlation_Guard: Limit max concurrent crypto positions to 2 (slot 3 reserved for US equities/uncorrelated)
5. Combo_Ranking_Scaling: Quality Ranking + Dynamic Scaling
6. Combo_Ranking_CorrGuard: Quality Ranking + Correlation Guard
7. Combo_Scaling_CorrGuard: Dynamic Scaling + Correlation Guard
8. Full_Synergy: Quality Ranking + Dynamic Scaling + Correlation Guard
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import copy
import json
import tempfile
from typing import Dict, List, Tuple, Any, Optional
from datetime import timedelta, datetime, timezone
from collections import defaultdict

import pandas as pd

from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine, RiskVerdict
from core.coordinator import PortfolioCoordinator, resolve_symbol_config, CoordinatorDecision, CycleReport
from core.signals import SignalStatus, SignalDirection, TradingSignal
from core.data_loader import fetch_ohlcv, is_crypto_symbol
import strategies.momentum as mom_mod
import strategies.mean_reversion as mr_mod
from strategies.momentum import compute_indicators, MomentumStrategy
from strategies.mean_reversion import compute_mean_reversion_indicators, MeanReversionStrategy
from research.backtest_profiles import summarize

TF_DELTA = {
    "1h": timedelta(hours=1),
    "15m": timedelta(minutes=15),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


class EnhancedRiskEngine(RiskEngine):
    """Risk engine with optional Dynamic Size Scaling and Cross-Asset Correlation Guard."""

    def __init__(
        self,
        *args,
        dynamic_scaling: bool = False,
        max_crypto_positions: int = 3,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.dynamic_scaling = dynamic_scaling
        self.max_crypto_positions = max_crypto_positions

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
        # Cross-Asset Correlation Guard
        if is_crypto_symbol(symbol):
            current_crypto_pos = sum(1 for sym in active_positions if is_crypto_symbol(sym))
            if current_crypto_pos >= self.max_crypto_positions:
                return RiskVerdict(
                    approved=False,
                    reason=f"Correlation Guard: Max crypto positions ({self.max_crypto_positions}) reached",
                )

        if not self.dynamic_scaling:
            return super().validate_proposed_entry(
                symbol=symbol,
                side=side,
                strategy_id=strategy_id,
                entry_price=entry_price,
                suggested_stop=suggested_stop,
                atr=atr,
                atr_multiplier=atr_multiplier,
                equity=equity,
                cash=cash,
                active_positions=active_positions,
                fee_pct=fee_pct,
                requested_risk_pct=requested_risk_pct,
            )

        # Dynamic Scaling implementation
        healthy, breaker_reason = self.check_circuit_breakers(equity)
        if not healthy:
            return RiskVerdict(approved=False, reason=breaker_reason)

        if symbol in active_positions:
            return RiskVerdict(approved=False, reason=f"Position already open for {symbol}")

        if len(active_positions) >= self.max_open_positions:
            return RiskVerdict(
                approved=False,
                reason=f"Max positions limit reached: {len(active_positions)}/{self.max_open_positions}",
            )

        is_short = (side.upper() == "SHORT")
        units, initial_stop, dollar_risk = self.calculate_position(
            equity=equity,
            entry_price=entry_price,
            atr=atr,
            atr_multiplier=atr_multiplier,
            risk_pct=requested_risk_pct,
            is_short=is_short,
        )

        order_cost = units * entry_price
        total_order_cost = order_cost * (1.0 + fee_pct)

        # Single asset exposure cap
        max_asset_cost = equity * self.max_single_asset_exposure_pct
        if total_order_cost > max_asset_cost:
            scaled_units = max_asset_cost / (entry_price * (1.0 + fee_pct))
            units = min(units, scaled_units)
            order_cost = units * entry_price
            total_order_cost = order_cost * (1.0 + fee_pct)
            dollar_risk = units * abs(entry_price - initial_stop)

        # Total portfolio exposure cap
        current_committed = sum(p["units"] * p["entry_price"] for p in active_positions.values())
        if (current_committed + order_cost) > (equity * self.max_total_exposure_pct):
            remaining_cap = max(0.0, (equity * self.max_total_exposure_pct) - current_committed)
            if remaining_cap <= 0:
                return RiskVerdict(approved=False, reason="Total exposure cap reached")
            scaled_units = remaining_cap / (entry_price * (1.0 + fee_pct))
            units = min(units, scaled_units)
            order_cost = units * entry_price
            total_order_cost = order_cost * (1.0 + fee_pct)
            dollar_risk = units * abs(entry_price - initial_stop)

        # Strategy allocation limit check with scale-down
        strategy_committed = sum(
            p["units"] * p["entry_price"]
            for p in active_positions.values()
            if p.get("strategy_id") == strategy_id
        )
        max_strat_dollars = equity * self.max_strategy_allocation_pct
        if (strategy_committed + order_cost) > max_strat_dollars:
            remaining_strat_cap = max(0.0, max_strat_dollars - strategy_committed)
            if remaining_strat_cap < (equity * 0.05):  # under 5% account equity
                return RiskVerdict(
                    approved=False,
                    reason=f"Strategy '{strategy_id}' allocation limit reached ({self.max_strategy_allocation_pct*100:.1f}%)",
                )
            scaled_units = remaining_strat_cap / (entry_price * (1.0 + fee_pct))
            units = min(units, scaled_units)
            order_cost = units * entry_price
            total_order_cost = order_cost * (1.0 + fee_pct)
            dollar_risk = units * abs(entry_price - initial_stop)

        # Available cash check
        if total_order_cost > cash:
            if cash < (equity * 0.05):
                return RiskVerdict(approved=False, reason=f"Insufficient cash: available ${cash:,.2f}")
            scaled_units = cash / (entry_price * (1.0 + fee_pct))
            units = min(units, scaled_units)
            order_cost = units * entry_price
            total_order_cost = order_cost * (1.0 + fee_pct)
            dollar_risk = units * abs(entry_price - initial_stop)

        if units <= 0:
            return RiskVerdict(approved=False, reason="Position size after scaling is 0")

        return RiskVerdict(
            approved=True,
            reason="Scaled order approved within allocation capacity",
            suggested_units=units,
            suggested_stop=initial_stop,
            dollar_risk=dollar_risk,
        )


class QualityRankingCoordinator(PortfolioCoordinator):
    """Coordinator with optional Signal Quality Ranking."""

    def __init__(self, *args, quality_ranking: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.quality_ranking = quality_ranking

    def run_cycle(self, market_data: Dict[str, pd.DataFrame]) -> CycleReport:
        if not self.quality_ranking:
            return super().run_cycle(market_data)

        now = datetime.now(timezone.utc)
        current_prices: Dict[str, float] = {}
        decisions: List[CoordinatorDecision] = []

        # 1. First, process any existing open positions (exits/trailing stops)
        remaining_symbols = {}
        for symbol, df in market_data.items():
            if symbol in self.account.positions:
                dec = self.process_symbol(symbol, df, current_prices)
                decisions.append(dec)
            else:
                remaining_symbols[symbol] = df

        # 2. Score remaining candidate symbols by breakout quality & trend strength
        scored_candidates = []
        for symbol, df in remaining_symbols.items():
            if df.empty or len(df) < 5:
                continue
            cur_price = float(df.iloc[-1]["close"])
            current_prices[symbol] = cur_price
            sym_cfg, mom_eng, mr_eng = self._engines_for(symbol)
            mom_sig = mom_eng.evaluate(symbol, df, existing_position=None)
            mr_sig = mr_eng.evaluate(symbol, df, existing_position=None)
            chosen_sig, _ = self.reconcile_signals(symbol, mom_sig, mr_sig)

            # Score signals
            score = 0.0
            if chosen_sig and chosen_sig.status == SignalStatus.ENTRY:
                # Volume surge component
                vol_ratio = 1.0
                if "volume" in df and len(df) >= 20:
                    avg_v = df["volume"].iloc[-20:].mean()
                    cur_v = df["volume"].iloc[-1]
                    vol_ratio = (cur_v / avg_v) if avg_v > 0 else 1.0

                # Trend extension above EMA50 component
                trend_dist = 0.0
                ema_col = df.get("ema_filter")
                if ema_col is not None and not ema_col.empty and not pd.isna(ema_col.iloc[-1]):
                    ema_val = float(ema_col.iloc[-1])
                    if ema_val > 0:
                        trend_dist = (cur_price - ema_val) / ema_val

                score = (chosen_sig.signal_strength * 3.0) + (min(3.0, vol_ratio) * 2.0) + (max(0.0, trend_dist) * 15.0)

            scored_candidates.append((score, symbol, df))

        # Sort highest quality score first
        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        conflicts = 0
        executed = 0
        signals_count = 0

        for score, symbol, df in scored_candidates:
            decision = self.process_symbol(symbol, df, current_prices)
            decisions.append(decision)

            if decision.action == "CONFLICT":
                conflicts += 1
            if decision.action in ("ENTER_LONG", "ENTER_SHORT", "EXIT"):
                executed += 1
            if decision.signal is not None and decision.signal.is_actionable:
                signals_count += 1

        total_equity = self.account.get_total_equity(current_prices)

        return CycleReport(
            timestamp=now,
            symbols_evaluated=len(market_data),
            signals_generated=signals_count,
            conflicts_detected=conflicts,
            orders_executed=executed,
            decisions=decisions,
            portfolio_equity=total_equity,
            available_cash=self.account.cash,
            open_positions=len(self.account.positions),
        )


def run_experiment():
    with open("config/active_config.json") as f:
        cfg = json.load(f)

    watchlist = cfg.get("watchlist", [])
    days = 90
    initial = 10000.0

    print(f"Loading {days} days of data for {len(watchlist)} assets...")
    data = {}
    for sym in watchlist:
        tf = "1h"
        try:
            df = fetch_ohlcv(symbol=sym, timeframe=tf, days=days, force_refresh=False)
            if df is not None and not df.empty:
                df = df.sort_index()
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                df.index = df.index + TF_DELTA[tf]
                data[(sym, tf)] = df[~df.index.duplicated(keep="last")]
        except Exception as e:
            print(f"Error loading {sym}: {e}")

    # Pre-compute indicators
    series = {}
    for sym in watchlist:
        tf = "1h"
        if (sym, tf) not in data:
            continue
        raw = data[(sym, tf)]
        sc = resolve_symbol_config(cfg, sym)
        m_df = compute_indicators(
            raw,
            breakout_lookback=sc.get("breakout_lookback", 24),
            ema_trend_filter=sc.get("ema_trend_filter", 50),
            atr_period=sc.get("atr_period", 14),
        )
        r_df = compute_mean_reversion_indicators(
            raw,
            bb_period=sc.get("bb_period", 20),
            bb_std=sc.get("bb_std", 2.0),
            rsi_period=sc.get("rsi_period", 14),
            atr_period=sc.get("atr_period", 14),
            adx_period=sc.get("adx_period", 14),
        )
        for col in ("bb_sma", "bb_upper", "bb_lower", "bb_width", "z_score", "rsi", "adx"):
            m_df[col] = r_df[col]
        series[sym] = m_df

    experiments = {
        "1. Baseline (Active)":     {"ranking": False, "scaling": False, "crypto_cap": 3},
        "2. Quality Ranking":       {"ranking": True,  "scaling": False, "crypto_cap": 3},
        "3. Dynamic Scaling":       {"ranking": False, "scaling": True,  "crypto_cap": 3},
        "4. Correlation Guard":     {"ranking": False, "scaling": False, "crypto_cap": 2},
        "5. Ranking + Scaling":     {"ranking": True,  "scaling": True,  "crypto_cap": 3},
        "6. Ranking + CorrGuard":   {"ranking": True,  "scaling": False, "crypto_cap": 2},
        "7. Scaling + CorrGuard":   {"ranking": False, "scaling": True,  "crypto_cap": 2},
        "8. Full Synergy (All 3)":  {"ranking": True,  "scaling": True,  "crypto_cap": 2},
    }

    results = []

    for name, params in experiments.items():
        tmp = Path(tempfile.mkdtemp(prefix=f"bt_{name[:6]}_"))
        account = PaperAccount(initial_capital=initial, portfolio_file=tmp / "portfolio.json")
        account._save = lambda: None

        risk = EnhancedRiskEngine(
            default_risk_pct=cfg.get("account_risk_pct", 0.02),
            max_open_positions=cfg.get("max_concurrent_positions", 3),
            max_strategy_allocation_pct=cfg.get("max_strategy_allocation_pct", 0.75),
            dynamic_scaling=params["scaling"],
            max_crypto_positions=params["crypto_cap"],
        )

        test_cfg = copy.deepcopy(cfg)
        test_cfg["max_stale_minutes"] = 0
        coord = QualityRankingCoordinator(account, risk, test_cfg, quality_ranking=params["ranking"])

        WINDOW = 60
        WARMUP = 150

        try:
            mom_mod.compute_indicators = lambda df, **_: df
            mr_mod.compute_mean_reversion_indicators = lambda df, **_: df

            start_idx = {sym: min(WARMUP, len(df) - 1) for sym, df in series.items()}
            timeline = sorted(set().union(*[set(df.index[start_idx[sym]:]) for sym, df in series.items()]))
            pos_idx = dict(start_idx)

            last_price: Dict[str, float] = {}
            equity_curve = []
            cur_day = None

            for t in timeline:
                if t.date() != cur_day:
                    cur_day = t.date()
                    risk.daily_start_equity = 0.0

                market = {}
                for sym, df in series.items():
                    i = pos_idx[sym]
                    if i < len(df) and df.index[i] == t:
                        market[sym] = df.iloc[max(0, i - WINDOW + 1): i + 1]
                        last_price[sym] = float(df.iloc[i]["close"])
                        pos_idx[sym] = i + 1

                if not market:
                    continue

                prev_open = set(account.positions.keys())
                coord.run_cycle(market)
                for sym in set(account.positions.keys()) - prev_open:
                    account.positions[sym]["entry_time"] = t.isoformat()

                equity_curve.append((t, account.get_total_equity(dict(last_price))))

        finally:
            mom_mod.compute_indicators = compute_indicators
            mr_mod.compute_mean_reversion_indicators = compute_mean_reversion_indicators

        # Close lingering positions
        for sym in list(account.positions):
            if sym in last_price:
                account.close_position(sym, last_price[sym] * 0.9996, exit_reason="End of backtest")

        summary = summarize(name, account, equity_curve, initial)
        results.append(summary)

    print("\n" + "=" * 98)
    print(f"{'CONFIGURATION':<25} | {'FINAL EQUITY':<14} | {'NET RETURN':<12} | {'WIN RATE':<10} | {'PROFIT FACTOR':<13} | {'MAX DD':<8} | {'TRADES'}")
    print("-" * 98)
    for r in results:
        pf_str = f"{r['profit_factor']:>12.2f}" if r['profit_factor'] is not None else f"{'N/A':>12}"
        print(
            f"{r['scenario']:<25} | "
            f"${r['final_equity']:>10.2f}    | "
            f"+{r['return_pct']:>7.2f}%   | "
            f"{r['win_rate_pct']:>8.1f}% | "
            f"{pf_str} | "
            f"{r['max_drawdown_pct']:>6.2f}% | "
            f"{r['trades']:>6}"
        )
    print("=" * 98)


if __name__ == "__main__":
    run_experiment()
