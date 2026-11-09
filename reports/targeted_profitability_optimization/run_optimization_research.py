"""Systematic Targeted Optimization Research & Validation Engine.

Evaluates Current Active, Candidate PROP-ED02E10E, and new optimized candidates
across multiple dimensions:
1. In-Sample Window 1 (-180d to -90d) and Out-of-Sample Window 2 (-90d to 0d).
2. Standalone strategies (Momentum, Mean Reversion, Stat Arb, Basis Yield).
3. 4 Cost Stress Scenarios (1.0x standard, 1.5x friction, 2.0x severe friction, 0.15% slippage).
4. Statistical moments, PSR, DSR, Sortino, Calmar.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "research"))

import research.run_shifted_oos_validation as oos
from core.coordinator import PortfolioCoordinator, resolve_symbol_config
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.institutional_stress_tester import calculate_psr_and_dsr

CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
OOS_REPORT = PROJECT_ROOT / "reports" / "oos_validation_report.json"
OUT_DIR = PROJECT_ROOT / "reports" / "targeted_profitability_optimization"


def apply_overrides(cfg: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Apply overrides at top level AND inside every asset profile that defines the key."""
    out = json.loads(json.dumps(cfg))
    for k, v in overrides.items():
        out[k] = v
        for prof in (out.get("asset_profiles") or {}).values():
            if k in prof:
                prof[k] = v
        for sym_cfg in (out.get("symbol_configs") or {}).values():
            if isinstance(sym_cfg, dict) and k in sym_cfg:
                sym_cfg[k] = v
    return out


def evaluate_scenario(
    cfg: Dict[str, Any],
    frames: Dict[str, pd.DataFrame],
    universe: List[str],
    strategies: List[str],
    cost_scenario: Dict[str, float],
    window: str = "W2",
) -> Dict[str, Any]:
    """Run single replay with exact fee and slippage settings."""
    fee_pct = float(cost_scenario.get("fee_pct", 0.0006))
    slip_pct = float(cost_scenario.get("slippage_pct", 0.0004))

    oos.TAKER_FEE = fee_pct
    oos.TAKER_SLIP = slip_pct

    c = json.loads(json.dumps(cfg))
    c["fee_pct"] = fee_pct
    c["slippage_pct"] = slip_pct

    win1, win2, _, _, _ = oos.split_selection_and_oos_windows(frames, c)
    target_window = win1 if window == "W1" else win2

    t0 = time.time()
    replay = oos.run_coordinator_window_replay(
        window_frames=target_window,
        universe=universe,
        base_cfg=c,
        enabled_strategies=strategies,
        initial_capital=10000.0,
    )
    elapsed = round(time.time() - t0, 2)

    net_ret = float(replay.get("net_return_pct", 0.0))
    max_dd = float(replay.get("max_drawdown_pct", 0.0))
    ann_ret = net_ret * (365.0 / 90.0)
    calmar = round(ann_ret / max_dd, 2) if max_dd > 0 else (99.0 if ann_ret > 0 else 0.0)

    # Statistical moments and DSR
    eq_curve = replay.get("equity_curve", [])
    psr_val, dsr_val, skew_val, kurt_val = 0.0, 0.0, 0.0, 0.0
    if eq_curve:
        eq_vals = [e[1] if isinstance(e, (list, tuple)) else e.get("equity", 10000.0) for e in eq_curve]
        eq_series = pd.Series(eq_vals)
        hourly_returns = eq_series.pct_change().dropna().values
        if len(hourly_returns) >= 10:
            psr, dsr, exp_max_sr, stats = calculate_psr_and_dsr(
                returns=hourly_returns,
                annualization_factor=252.0 * 6.5,
                num_trials=72,
                benchmark_sr=0.0,
            )
            psr_val = float(psr)
            dsr_val = float(dsr)
            skew_val = float(stats.get("skewness", 0.0))
            kurt_val = float(stats.get("kurtosis", 0.0))

    return {
        "net_return_pct": net_ret,
        "net_pnl": float(replay.get("net_pnl", 0.0)),
        "ending_equity": float(replay.get("final_equity", 10000.0)),
        "sharpe_ratio": float(replay.get("sharpe_ratio", 0.0)),
        "sortino_ratio": float(replay.get("sortino_ratio", 0.0)),
        "calmar_ratio": calmar,
        "max_drawdown_pct": max_dd,
        "total_trades": int(replay.get("total_trades", 0)),
        "directional_trades": int(replay.get("directional_trades", 0)),
        "basis_accruals": int(replay.get("basis_accruals", 0)),
        "win_rate_pct": float(replay.get("win_rate_pct", 0.0)),
        "directional_win_rate_pct": float(replay.get("directional_win_rate_pct", 0.0)),
        "profit_factor": float(replay.get("profit_factor", 0.0)),
        "directional_profit_factor": float(replay.get("directional_profit_factor", 0.0)),
        "expectancy_per_trade": float(replay.get("expectancy_per_trade", 0.0)),
        "directional_expectancy_per_trade": float(replay.get("directional_expectancy_per_trade", 0.0)),
        "total_fees_paid": float(replay.get("total_fees_paid", 0.0)),
        "elapsed_seconds": elapsed,
        "by_strategy": replay.get("by_strategy", {}),
        "signals_by_strategy": replay.get("signals_by_strategy", {}),
        "risk_rejections": int(replay.get("risk_rejections", 0)),
        "psr": round(psr_val, 4),
        "dsr": round(dsr_val, 4),
        "skewness": round(skew_val, 3),
        "kurtosis": round(kurt_val, 3),
    }


def main():
    print("=" * 80)
    print("WEB4 TARGETED PROFITABILITY OPTIMIZATION & ROBUSTNESS RESEARCH")
    print("=" * 80)

    base_cfg = json.loads(CONFIG_PATH.read_text())
    frames = oos.load_180d_candidate_frames(base_cfg)
    win1, win2, _, _, _ = oos.split_selection_and_oos_windows(frames, base_cfg)
    universe, _ = oos.select_universe_from_window1(win1, base_cfg)

    print(f"Loaded 180d frames for {len(frames)} symbols.")
    print(f"Selected universe: {universe}")

    candidate_pairs = [
        ["NVDA", "AAPL"],
        ["WULF", "CIFR"],
        ["IREN", "CIFR"],
        ["BNB/USDT", "LINK/USDT"],
        ["SOL/USDT", "LINK/USDT"],
        ["MU", "MCHP"],
    ]

    active_pairs = [
        ["GLD", "SLV"],
        ["NVDA", "AMD"],
        ["NVDA", "SPY"],
        ["BTC/USDT", "ETH/USDT"],
        ["SOL/USDT", "AVAX/USDT"],
        ["XLE", "TLT"],
    ]

    standard_costs = {"fee_pct": 0.0006, "slippage_pct": 0.0004}

    # Define test configurations
    configs_to_test = {
        "Current_Active": {},
        "Candidate_PROP_ED02E10E": {
            "adx_chop_threshold": 26.0,
            "bb_std": 1.8,
            "stat_arb_entry_z": 2.5,
            "stat_arb_min_edge_cost_ratio": 2.5,
            "pairs_universe": candidate_pairs,
        },
        "Optimized_Balanced": {
            # Fix the dead-zone by aligning chop (24) with trend threshold (28)
            "adx_chop_threshold": 24.0,
            "adx_trend_threshold": 28.0,
            "bb_std": 1.9,
            "stat_arb_entry_z": 2.5,
            "stat_arb_min_edge_cost_ratio": 2.5,
            "pairs_universe": candidate_pairs,
        },
        "Optimized_Conservative": {
            "adx_chop_threshold": 25.0,
            "adx_trend_threshold": 28.0,
            "bb_std": 1.8,
            "stat_arb_entry_z": 2.5,
            "stat_arb_min_edge_cost_ratio": 2.5,
            "pairs_universe": candidate_pairs,
        },
        "Optimized_ActivePairs_Gated": {
            # Use active pairs universe with the 2.5x hurdle gate to evaluate if active pairs can trade
            "adx_chop_threshold": 24.0,
            "adx_trend_threshold": 28.0,
            "bb_std": 1.9,
            "stat_arb_entry_z": 2.5,
            "stat_arb_min_edge_cost_ratio": 2.5,
            "pairs_universe": active_pairs,
        },
    }

    all4 = ["momentum", "mean_reversion", "stat_arb_pairs", "basis_yield"]

    print("\n--- PHASE 1 & 2: IN-SAMPLE (W1) vs OUT-OF-SAMPLE (W2) PERFORMANCE ---")
    w1_w2_results = {}
    for cname, covr in configs_to_test.items():
        cfg = apply_overrides(base_cfg, covr)
        r_w1 = evaluate_scenario(cfg, frames, universe, all4, standard_costs, window="W1")
        r_w2 = evaluate_scenario(cfg, frames, universe, all4, standard_costs, window="W2")
        w1_w2_results[cname] = {"W1": r_w1, "W2": r_w2}

        print(
            f"{cname:<30} | W1: {r_w1['net_return_pct']:+6.2f}% (DD: {r_w1['max_drawdown_pct']:5.2f}%, Tr: {r_w1['directional_trades']:>3}) | "
            f"W2: {r_w2['net_return_pct']:+6.2f}% (DD: {r_w2['max_drawdown_pct']:5.2f}%, Sharpe: {r_w2['sharpe_ratio']:4.2f}, Tr: {r_w2['directional_trades']:>3}, Fees: ${r_w2['total_fees_paid']:6.2f})"
        )

    print("\n--- PHASE 3: STANDALONE STRATEGY ATTRIBUTION (W2) ---")
    strategy_results = {}
    for cname in ["Current_Active", "Candidate_PROP_ED02E10E", "Optimized_Balanced", "Optimized_ActivePairs_Gated"]:
        cfg = apply_overrides(base_cfg, configs_to_test[cname])
        strategy_results[cname] = {}
        print(f"\nConfiguration: {cname}")
        for strat in ["momentum", "mean_reversion", "stat_arb_pairs", "basis_yield"]:
            r_strat = evaluate_scenario(cfg, frames, universe, [strat], standard_costs, window="W2")
            strategy_results[cname][strat] = r_strat
            print(
                f"  {strat:<16} | Net: {r_strat['net_return_pct']:+6.2f}% | PnL: ${r_strat['net_pnl']:8.2f} | "
                f"Trades: {r_strat['total_trades']:>3} | WinRate: {r_strat['win_rate_pct']:5.1f}% | "
                f"PF: {r_strat['profit_factor']:4.2f} | Fees: ${r_strat['total_fees_paid']:6.2f}"
            )

    print("\n--- PHASE 4: COST STRESS TESTING (W2) ---")
    stress_scenarios = [
        ("standard_1.0x", {"fee_pct": 0.0006, "slippage_pct": 0.0004}),
        ("friction_1.5x", {"fee_pct": 0.0009, "slippage_pct": 0.0006}),
        ("friction_2.0x", {"fee_pct": 0.0012, "slippage_pct": 0.0008}),
        ("slippage_shock_0.15pct", {"fee_pct": 0.0006, "slippage_pct": 0.0015}),
    ]
    stress_results = {}
    for cname in ["Current_Active", "Candidate_PROP_ED02E10E", "Optimized_Balanced", "Optimized_ActivePairs_Gated"]:
        cfg = apply_overrides(base_cfg, configs_to_test[cname])
        stress_results[cname] = {}
        print(f"\nConfiguration: {cname}")
        for sname, scosts in stress_scenarios:
            r_stress = evaluate_scenario(cfg, frames, universe, all4, scosts, window="W2")
            stress_results[cname][sname] = r_stress
            print(
                f"  {sname:<24} | Net: {r_stress['net_return_pct']:+6.2f}% | PnL: ${r_stress['net_pnl']:8.2f} | "
                f"Sharpe: {r_stress['sharpe_ratio']:4.2f} | MaxDD: {r_stress['max_drawdown_pct']:5.2f}% | "
                f"Trades: {r_stress['directional_trades']:>3} | Fees: ${r_stress['total_fees_paid']:6.2f}"
            )

    # Save all results to disk
    full_output = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "configs_tested": configs_to_test,
        "w1_w2_results": w1_w2_results,
        "strategy_results": strategy_results,
        "stress_results": stress_results,
    }

    results_file = OUT_DIR / "optimization_research_results.json"
    with open(results_file, "w") as f:
        json.dump(full_output, f, indent=2)
    print(f"\nSuccessfully written research results to {results_file}")


if __name__ == "__main__":
    main()
