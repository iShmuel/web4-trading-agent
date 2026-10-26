"""Backtest script comparing strategy allocation limit variations on the 90-day full watchlist.

Scenarios tested:
1. Baseline_Cap60: Current 60% hard cap (rejects if exceeds)
2. Cap75_Hard: 75% hard cap (rejects if exceeds)
3. Cap85_Hard: 85% hard cap (matches total exposure cap)
4. Cap60_Scaled: 60% cap with dynamic scale-down (takes smaller position rather than rejecting)
5. Cap75_Scaled: 75% cap with dynamic scale-down
"""

import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import copy
import json
import tempfile
from typing import Dict, List, Tuple, Any, Optional
from datetime import timedelta

import pandas as pd

from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine, RiskVerdict
from core.coordinator import PortfolioCoordinator, resolve_symbol_config
from core.data_loader import fetch_ohlcv
import strategies.momentum as mom_mod
import strategies.mean_reversion as mr_mod
from strategies.momentum import compute_indicators
from strategies.mean_reversion import compute_mean_reversion_indicators
from research.backtest_profiles import summarize

TF_DELTA = {
    "1h": timedelta(hours=1),
    "15m": timedelta(minutes=15),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


class ScalingRiskEngine(RiskEngine):
    """Custom risk engine that optionally scales down units to fit strategy allocation."""

    def __init__(self, *args, scale_to_strategy_cap: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.scale_to_strategy_cap = scale_to_strategy_cap

    def check_order(
        self,
        equity: float,
        cash: float,
        symbol: str,
        side: str,
        entry_price: float,
        atr: float,
        atr_multiplier: float = 3.0,
        strategy_id: str = "momentum",
        active_positions: Optional[Dict[str, Any]] = None,
        fee_pct: float = 0.0008,
        requested_risk_pct: Optional[float] = None,
    ) -> RiskVerdict:
        if not self.scale_to_strategy_cap:
            return super().check_order(
                equity=equity,
                cash=cash,
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                atr=atr,
                atr_multiplier=atr_multiplier,
                strategy_id=strategy_id,
                active_positions=active_positions,
                fee_pct=fee_pct,
                requested_risk_pct=requested_risk_pct,
            )

        active_positions = active_positions or {}
        orig_strat_cap = self.max_strategy_allocation_pct
        self.max_strategy_allocation_pct = 1.0  # bypass step 7 in super
        verdict = super().check_order(
            equity=equity,
            cash=cash,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            atr=atr,
            atr_multiplier=atr_multiplier,
            strategy_id=strategy_id,
            active_positions=active_positions,
            fee_pct=fee_pct,
            requested_risk_pct=requested_risk_pct,
        )
        self.max_strategy_allocation_pct = orig_strat_cap

        if not verdict.approved:
            return verdict

        # Now apply strategy allocation with scale-down
        strategy_committed = sum(
            p["units"] * p["entry_price"]
            for p in active_positions.values()
            if p.get("strategy_id") == strategy_id
        )
        max_strat_dollars = equity * self.max_strategy_allocation_pct
        remaining_strat_cap = max(0.0, max_strat_dollars - strategy_committed)

        units = verdict.suggested_units
        order_cost = units * entry_price
        total_order_cost = order_cost * (1.0 + fee_pct)

        if total_order_cost > remaining_strat_cap:
            if remaining_strat_cap <= (equity * 0.05):  # tiny slice < 5% equity
                return RiskVerdict(
                    approved=False,
                    reason=f"Strategy '{strategy_id}' allocation limit reached ({self.max_strategy_allocation_pct*100:.1f}%)",
                )
            scaled_units = remaining_strat_cap / (entry_price * (1.0 + fee_pct))
            units = min(units, scaled_units)
            order_cost = units * entry_price
            dollar_risk = units * abs(entry_price - verdict.suggested_stop)

        if units <= 0:
            return RiskVerdict(approved=False, reason="Position size after scaling is 0")

        return RiskVerdict(
            approved=True,
            reason="Scaled order approved within strategy allocation",
            suggested_units=units,
            suggested_stop=verdict.suggested_stop,
            dollar_risk=dollar_risk,
        )


def run_test():
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

    scenarios = {
        "Baseline_Cap60": {"strat_cap": 0.60, "scale": False},
        "Cap75_Hard":     {"strat_cap": 0.75, "scale": False},
        "Cap85_Hard":     {"strat_cap": 0.85, "scale": False},
        "Cap60_Scaled":   {"strat_cap": 0.60, "scale": True},
        "Cap75_Scaled":   {"strat_cap": 0.75, "scale": True},
        "Cap85_Scaled":   {"strat_cap": 0.85, "scale": True},
    }

    results = []

    for name, params in scenarios.items():
        tmp = Path(tempfile.mkdtemp(prefix=f"bt_{name}_"))
        account = PaperAccount(initial_capital=initial, portfolio_file=tmp / "portfolio.json")
        account._save = lambda: None

        risk = ScalingRiskEngine(
            default_risk_pct=cfg.get("account_risk_pct", 0.02),
            max_open_positions=cfg.get("max_concurrent_positions", 3),
            max_strategy_allocation_pct=params["strat_cap"],
            scale_to_strategy_cap=params["scale"],
        )

        test_cfg = copy.deepcopy(cfg)
        test_cfg["max_stale_minutes"] = 0
        coord = PortfolioCoordinator(account, risk, test_cfg)

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

    print("\n" + "=" * 90)
    print(f"{'SCENARIO':<20} | {'FINAL EQUITY':<16} | {'RETURN %':<10} | {'WIN RATE':<10} | {'PROFIT FACTOR':<14} | {'MAX DD':<8} | {'TRADES'}")
    print("-" * 90)
    for r in results:
        pf_str = f"{r['profit_factor']:>13.2f}" if r['profit_factor'] is not None else f"{'N/A':>13}"
        print(
            f"{r['scenario']:<20} | "
            f"${r['final_equity']:>10.2f}      | "
            f"{r['return_pct']:>9.2f}% | "
            f"{r['win_rate_pct']:>8.1f}% | "
            f"{pf_str} | "
            f"{r['max_drawdown_pct']:>6.2f}% | "
            f"{r['trades']:>6}"
        )
    print("=" * 90)


if __name__ == "__main__":
    run_test()
