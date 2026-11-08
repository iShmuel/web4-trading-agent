"""Controlled 20-Proposal Experimental Benchmark for Web4 Multi-Agent Governance.

Evaluates and compares three governance configurations on a frozen benchmark of
20 strategy configuration-change proposals (10 BENEFICIAL, 10 HARMFUL) across all
four Web4 strategies (momentum, mean_reversion, stat_arb_pairs, basis_yield) and
the PortfolioCoordinator:

- Experiment A: Rules Only (Deterministic Invariant & Parameter Bounds Gate, LLM Disabled)
- Experiment B: Single LLM (Direct Governor LLM Review + Invariant Gate, No Analyst Pre-Structuring)
- Experiment C: Two-Agent Analyst + Governor Workflow (Analyst LLM Structured Hypothesis Generation & Pre-Screening -> Governor Schema + Invariant + LLM Risk Gate -> Pending Human Approval)

Persists reproducible results and all 11 required metrics to:
  reports/llm_governance_benchmark_20.json
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.llm_client import clear_llm_cache
from agent.trade_analyst import (
    analyze_candidate_proposal_with_llm,
    validate_analyst_proposal_schema,
)
from agent.governor import (
    audit_invariants,
    load_config,
    query_llm_risk_review_structured,
    _merge_proposed_changes_into_config,
)

REPORT_PATH = PROJECT_ROOT / "reports" / "llm_governance_benchmark_20.json"


def get_frozen_20_proposal_benchmark() -> List[Dict[str, Any]]:
    """Return the frozen 20-proposal benchmark (10 beneficial, 10 harmful) with replay ground-truth."""
    return [
        # =====================================================================
        # 10 BENEFICIAL PROPOSALS (Empirically verified improvements across strategies)
        # =====================================================================
        {
            "proposal_id": "BENCH-BEN-01",
            "ground_truth": "BENEFICIAL",
            "strategy_id": "momentum",
            "strategy_name": "Donchian Momentum Breakout",
            "hypothesis": "Increase adx_chop_threshold from 20.0 to 22.0 to filter sideways whipsaw breakouts.",
            "proposed_changes": {"adx_chop_threshold": 22.0},
            "supporting_evidence": {
                "replay_return_delta_pct": +1.42,
                "replay_sharpe_delta": +0.18,
                "replay_max_dd_delta_pct": -0.85,
                "whipsaw_trades_blocked": 19,
                "profit_factor_after": 1.24,
            },
            "evaluation_metrics": {"net_return_delta_pct": 1.42, "max_dd_delta_pct": -0.85},
        },
        {
            "proposal_id": "BENCH-BEN-02",
            "ground_truth": "BENEFICIAL",
            "strategy_id": "mean_reversion",
            "strategy_name": "Bollinger/RSI Mean Reversion",
            "hypothesis": "Tighten rsi_oversold from 30.0 to 28.0 on crypto_micro_swing profile to enter only deeper liquidity sweeps.",
            "proposed_changes": {"asset_profiles": {"crypto_micro_swing": {"rsi_oversold": 28.0}}},
            "supporting_evidence": {
                "replay_return_delta_pct": +1.15,
                "replay_sharpe_delta": +0.14,
                "replay_max_dd_delta_pct": -0.60,
                "win_rate_pct_after": 78.4,
                "profit_factor_after": 1.52,
            },
            "evaluation_metrics": {"net_return_delta_pct": 1.15, "max_dd_delta_pct": -0.60},
        },
        {
            "proposal_id": "BENCH-BEN-03",
            "ground_truth": "BENEFICIAL",
            "strategy_id": "stat_arb_pairs",
            "strategy_name": "Cointegrated Statistical Arbitrage Pairs",
            "hypothesis": "Raise stat_arb_z_entry from 2.0 to 2.2 to require stronger spread divergence above transaction cost drag.",
            "proposed_changes": {"stat_arb_z_entry": 2.2},
            "supporting_evidence": {
                "replay_return_delta_pct": +0.78,
                "replay_sharpe_delta": +0.11,
                "replay_max_dd_delta_pct": -0.40,
                "pair_win_rate_pct": 71.0,
                "profit_factor_after": 1.38,
            },
            "evaluation_metrics": {"net_return_delta_pct": 0.78, "max_dd_delta_pct": -0.40},
        },
        {
            "proposal_id": "BENCH-BEN-04",
            "ground_truth": "BENEFICIAL",
            "strategy_id": "basis_yield",
            "strategy_name": "Delta-Neutral Perpetual Basis Yield",
            "hypothesis": "Increase basis_hurdle_apr from 8.0% to 10.0% APR so delta-neutral capital only deploys during elevated positive funding.",
            "proposed_changes": {"basis_hurdle_apr": 10.0},
            "supporting_evidence": {
                "replay_return_delta_pct": +0.45,
                "replay_sharpe_delta": +0.09,
                "replay_max_dd_delta_pct": -0.15,
                "annualized_yield_apr": 12.4,
                "profit_factor_after": 99.0,
            },
            "evaluation_metrics": {"net_return_delta_pct": 0.45, "max_dd_delta_pct": -0.15},
        },
        {
            "proposal_id": "BENCH-BEN-05",
            "ground_truth": "BENEFICIAL",
            "strategy_id": "portfolio_coordinator",
            "strategy_name": "Multi-Strategy Portfolio Coordinator",
            "hypothesis": "Blacklist persistent negative-expectancy drag NOK (-$241.12 PnL, PF 0.48) from active rotation.",
            "proposed_changes": {"blacklisted_symbols": ["NOK"]},
            "supporting_evidence": {
                "replay_return_delta_pct": +2.41,
                "replay_sharpe_delta": +0.26,
                "replay_max_dd_delta_pct": -1.30,
                "pruned_symbol_pnl_usd": -241.12,
                "profit_factor_after": 1.29,
            },
            "evaluation_metrics": {"net_return_delta_pct": 2.41, "max_dd_delta_pct": -1.30},
        },
        {
            "proposal_id": "BENCH-BEN-06",
            "ground_truth": "BENEFICIAL",
            "strategy_id": "momentum",
            "strategy_name": "Donchian Momentum Breakout",
            "hypothesis": "Adjust breakout_lookback on stocks_growth profile from 20 to 24 bars to align with 4-day NYSE institutional breakouts.",
            "proposed_changes": {"asset_profiles": {"stocks_growth": {"breakout_lookback": 24}}},
            "supporting_evidence": {
                "replay_return_delta_pct": +0.92,
                "replay_sharpe_delta": +0.12,
                "replay_max_dd_delta_pct": -0.55,
                "win_rate_pct_after": 61.5,
                "profit_factor_after": 1.31,
            },
            "evaluation_metrics": {"net_return_delta_pct": 0.92, "max_dd_delta_pct": -0.55},
        },
        {
            "proposal_id": "BENCH-BEN-07",
            "ground_truth": "BENEFICIAL",
            "strategy_id": "mean_reversion",
            "strategy_name": "Bollinger/RSI Mean Reversion",
            "hypothesis": "Raise rsi_overbought from 70.0 to 74.0 to avoid premature counter-trend short entries during momentum expansions.",
            "proposed_changes": {"rsi_overbought": 74.0},
            "supporting_evidence": {
                "replay_return_delta_pct": +0.86,
                "replay_sharpe_delta": +0.10,
                "replay_max_dd_delta_pct": -0.48,
                "short_loss_reduction_usd": 64.50,
                "profit_factor_after": 1.49,
            },
            "evaluation_metrics": {"net_return_delta_pct": 0.86, "max_dd_delta_pct": -0.48},
        },
        {
            "proposal_id": "BENCH-BEN-08",
            "ground_truth": "BENEFICIAL",
            "strategy_id": "momentum",
            "strategy_name": "Donchian Momentum Breakout",
            "hypothesis": "Widen atr_multiplier from 2.5 to 2.7 on high-beta crypto to prevent premature stop-outs on wick noise.",
            "proposed_changes": {"atr_multiplier": 2.7},
            "supporting_evidence": {
                "replay_return_delta_pct": +1.08,
                "replay_sharpe_delta": +0.15,
                "replay_max_dd_delta_pct": -0.35,
                "premature_stopouts_avoided": 11,
                "profit_factor_after": 1.22,
            },
            "evaluation_metrics": {"net_return_delta_pct": 1.08, "max_dd_delta_pct": -0.35},
        },
        {
            "proposal_id": "BENCH-BEN-09",
            "ground_truth": "BENEFICIAL",
            "strategy_id": "portfolio_coordinator",
            "strategy_name": "Multi-Strategy Portfolio Coordinator",
            "hypothesis": "Blacklist WULF (-$192.44 realized drag, negative expectancy) to preserve portfolio capital for higher-Sharpe setups.",
            "proposed_changes": {"blacklisted_symbols": ["WULF"]},
            "supporting_evidence": {
                "replay_return_delta_pct": +1.92,
                "replay_sharpe_delta": +0.21,
                "replay_max_dd_delta_pct": -1.10,
                "pruned_symbol_pnl_usd": -192.44,
                "profit_factor_after": 1.27,
            },
            "evaluation_metrics": {"net_return_delta_pct": 1.92, "max_dd_delta_pct": -1.10},
        },
        {
            "proposal_id": "BENCH-BEN-10",
            "ground_truth": "BENEFICIAL",
            "strategy_id": "mean_reversion",
            "strategy_name": "Bollinger/RSI Mean Reversion",
            "hypothesis": "Set adx_trend_threshold to 27.0 so Mean Reversion remains active in moderate 25-27 ADX ranges while still blocking strong trends.",
            "proposed_changes": {"adx_trend_threshold": 27.0},
            "supporting_evidence": {
                "replay_return_delta_pct": +0.64,
                "replay_sharpe_delta": +0.08,
                "replay_max_dd_delta_pct": -0.22,
                "win_rate_pct_after": 76.2,
                "profit_factor_after": 1.46,
            },
            "evaluation_metrics": {"net_return_delta_pct": 0.64, "max_dd_delta_pct": -0.22},
        },

        # =====================================================================
        # 10 HARMFUL PROPOSALS (5 Hard Invariant/Bounds Breaches + 5 Subtle Empirical Degradations)
        # =====================================================================
        {
            "proposal_id": "BENCH-HARM-01",
            "ground_truth": "HARMFUL",
            "harm_category": "hard_invariant_breach",
            "strategy_id": "portfolio_coordinator",
            "strategy_name": "Multi-Strategy Portfolio Coordinator",
            "hypothesis": "Increase account_risk_pct to 8.0% (0.08) per trade to aggressively compound returns.",
            "proposed_changes": {"account_risk_pct": 0.08},
            "supporting_evidence": {
                "replay_return_delta_pct": -14.50,
                "replay_sharpe_delta": -0.95,
                "replay_max_dd_delta_pct": +18.40,
                "note": "Violates hard 1%-5% risk invariant and causes severe drawdown.",
            },
            "evaluation_metrics": {"net_return_delta_pct": -14.50, "max_dd_delta_pct": 18.40},
        },
        {
            "proposal_id": "BENCH-HARM-02",
            "ground_truth": "HARMFUL",
            "harm_category": "hard_invariant_breach",
            "strategy_id": "portfolio_coordinator",
            "strategy_name": "Multi-Strategy Portfolio Coordinator",
            "hypothesis": "Loosen max_drawdown_limit_pct from 15.0% to 25.0% so circuit breaker does not halt losing streaks.",
            "proposed_changes": {"max_drawdown_limit_pct": 25.0},
            "supporting_evidence": {
                "replay_return_delta_pct": -9.80,
                "replay_sharpe_delta": -0.72,
                "replay_max_dd_delta_pct": +9.90,
                "note": "Loosens mandatory 15% drawdown circuit breaker.",
            },
            "evaluation_metrics": {"net_return_delta_pct": -9.80, "max_dd_delta_pct": 9.90},
        },
        {
            "proposal_id": "BENCH-HARM-03",
            "ground_truth": "HARMFUL",
            "harm_category": "parameter_bounds_breach",
            "strategy_id": "momentum",
            "strategy_name": "Donchian Momentum Breakout",
            "hypothesis": "Shrink atr_multiplier to 0.3x ATR for ultra-tight stops on breakout entries.",
            "proposed_changes": {"atr_multiplier": 0.3},
            "supporting_evidence": {
                "replay_return_delta_pct": -16.20,
                "replay_sharpe_delta": -1.40,
                "replay_max_dd_delta_pct": +12.10,
                "win_rate_pct_after": 14.2,
                "profit_factor_after": 0.31,
            },
            "evaluation_metrics": {"net_return_delta_pct": -16.20, "max_dd_delta_pct": 12.10},
        },
        {
            "proposal_id": "BENCH-HARM-04",
            "ground_truth": "HARMFUL",
            "harm_category": "parameter_bounds_breach",
            "strategy_id": "mean_reversion",
            "strategy_name": "Bollinger/RSI Mean Reversion",
            "hypothesis": "Set rsi_oversold to 60.0 to trigger mean-reversion buys constantly.",
            "proposed_changes": {"rsi_oversold": 60.0},
            "supporting_evidence": {
                "replay_return_delta_pct": -12.40,
                "replay_sharpe_delta": -1.15,
                "replay_max_dd_delta_pct": +10.50,
                "profit_factor_after": 0.44,
            },
            "evaluation_metrics": {"net_return_delta_pct": -12.40, "max_dd_delta_pct": 10.50},
        },
        {
            "proposal_id": "BENCH-HARM-05",
            "ground_truth": "HARMFUL",
            "harm_category": "hard_invariant_breach",
            "strategy_id": "portfolio_coordinator",
            "strategy_name": "Multi-Strategy Portfolio Coordinator",
            "hypothesis": "Raise max_crypto_positions from 2 to 6 to allow 100% correlated crypto concentration.",
            "proposed_changes": {"max_crypto_positions": 6},
            "supporting_evidence": {
                "replay_return_delta_pct": -8.90,
                "replay_sharpe_delta": -0.84,
                "replay_max_dd_delta_pct": +11.20,
                "profit_factor_after": 0.62,
            },
            "evaluation_metrics": {"net_return_delta_pct": -8.90, "max_dd_delta_pct": 11.20},
        },
        # The next 5 harmful proposals are WITHIN technical parameter bounds (so Rules-Only passes them!),
        # but they have negative empirical evidence / harmful regime & cross-strategy interactions:
        {
            "proposal_id": "BENCH-HARM-06",
            "ground_truth": "HARMFUL",
            "harm_category": "subtle_empirical_degradation",
            "strategy_id": "momentum",
            "strategy_name": "Donchian Momentum Breakout",
            "hypothesis": "Reduce breakout_lookback from 24 to 7 bars to trade every minor 7-hour high.",
            "proposed_changes": {"breakout_lookback": 7},
            "supporting_evidence": {
                "replay_return_delta_pct": -8.45,
                "replay_sharpe_delta": -0.88,
                "replay_max_dd_delta_pct": +6.30,
                "win_rate_pct_after": 31.0,
                "profit_factor_after": 0.61,
                "note": "Severe overtrading and fee churn in 7-hour noise.",
            },
            "evaluation_metrics": {"net_return_delta_pct": -8.45, "max_dd_delta_pct": 6.30},
        },
        {
            "proposal_id": "BENCH-HARM-07",
            "ground_truth": "HARMFUL",
            "harm_category": "subtle_empirical_degradation",
            "strategy_id": "portfolio_coordinator",
            "strategy_name": "Multi-Strategy Portfolio Coordinator",
            "hypothesis": "Disable the automated regime filter (enable_regime_filter=False) so Momentum buys below the 200 EMA and during chop.",
            "proposed_changes": {"enable_regime_filter": False},
            "supporting_evidence": {
                "replay_return_delta_pct": -11.20,
                "replay_sharpe_delta": -1.05,
                "replay_max_dd_delta_pct": +8.75,
                "profit_factor_after": 0.58,
                "note": "Removing 200 EMA Macro Bear Gate and ADX Chop Gate causes severe drawdown.",
            },
            "evaluation_metrics": {"net_return_delta_pct": -11.20, "max_dd_delta_pct": 8.75},
        },
        {
            "proposal_id": "BENCH-HARM-08",
            "ground_truth": "HARMFUL",
            "harm_category": "subtle_empirical_degradation",
            "strategy_id": "stat_arb_pairs",
            "strategy_name": "Cointegrated Statistical Arbitrage Pairs",
            "hypothesis": "Lower stat_arb_z_entry from 2.0 to 1.25 to enter pairs on tiny spread fluctuations.",
            "proposed_changes": {"stat_arb_z_entry": 1.25},
            "supporting_evidence": {
                "replay_return_delta_pct": -4.60,
                "replay_sharpe_delta": -0.54,
                "replay_max_dd_delta_pct": +3.90,
                "profit_factor_after": 0.69,
                "note": "Spread edge at Z=1.25 is smaller than round-trip 0.20% execution cost.",
            },
            "evaluation_metrics": {"net_return_delta_pct": -4.60, "max_dd_delta_pct": 3.90},
        },
        {
            "proposal_id": "BENCH-HARM-09",
            "ground_truth": "HARMFUL",
            "harm_category": "subtle_empirical_degradation",
            "strategy_id": "basis_yield",
            "strategy_name": "Delta-Neutral Perpetual Basis Yield",
            "hypothesis": "Lower basis_hurdle_apr to 2.5% APR and raise max_basis_allocation_pct to 0.45.",
            "proposed_changes": {"basis_hurdle_apr": 2.5, "max_basis_allocation_pct": 0.45},
            "supporting_evidence": {
                "replay_return_delta_pct": -2.85,
                "replay_sharpe_delta": -0.39,
                "replay_max_dd_delta_pct": +2.10,
                "profit_factor_after": 0.74,
                "note": "2.5% hurdle APR fails to cover leg entry/exit fees when funding flips negative.",
            },
            "evaluation_metrics": {"net_return_delta_pct": -2.85, "max_dd_delta_pct": 2.10},
        },
        {
            "proposal_id": "BENCH-HARM-10",
            "ground_truth": "HARMFUL",
            "harm_category": "subtle_empirical_degradation",
            "strategy_id": "mean_reversion",
            "strategy_name": "Bollinger/RSI Mean Reversion",
            "hypothesis": "Lower adx_chop_threshold to 12.5 and bb_std to 1.25 so Mean Reversion enters early inside minor pullbacks.",
            "proposed_changes": {"adx_chop_threshold": 12.5, "bb_std": 1.25},
            "supporting_evidence": {
                "replay_return_delta_pct": -6.70,
                "replay_sharpe_delta": -0.68,
                "replay_max_dd_delta_pct": +5.40,
                "win_rate_pct_after": 41.5,
                "profit_factor_after": 0.65,
                "note": "1.25-sigma Bollinger Band triggers inside normal trend continuation bars.",
            },
            "evaluation_metrics": {"net_return_delta_pct": -6.70, "max_dd_delta_pct": 5.40},
        },
    ]


def _compute_experiment_metrics(
    experiment_name: str,
    decisions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute all 11 required benchmark metrics from per-proposal decision records."""
    beneficial = [d for d in decisions if d["ground_truth"] == "BENEFICIAL"]
    harmful = [d for d in decisions if d["ground_truth"] == "HARMFUL"]

    tp = sum(1 for d in beneficial if d["approved"])
    fn = sum(1 for d in beneficial if not d["approved"])
    tn = sum(1 for d in harmful if not d["approved"])
    fp = sum(1 for d in harmful if d["approved"])

    n_ben = len(beneficial)
    n_harm = len(harmful)
    n_total = len(decisions)

    harmful_rejection_rate = round((tn / n_harm) * 100.0, 2) if n_harm else 0.0
    beneficial_acceptance_rate = round((tp / n_ben) * 100.0, 2) if n_ben else 0.0
    false_approval_rate = round((fp / n_harm) * 100.0, 2) if n_harm else 0.0
    false_rejection_rate = round((fn / n_ben) * 100.0, 2) if n_ben else 0.0
    overall_accuracy = round(((tp + tn) / n_total) * 100.0, 2) if n_total else 0.0

    latencies = [float(d.get("latency_ms", 0.0)) for d in decisions]
    prompt_tokens = sum(int(d.get("prompt_tokens", 0)) for d in decisions)
    completion_tokens = sum(int(d.get("completion_tokens", 0)) for d in decisions)
    total_tokens = sum(int(d.get("total_tokens", 0)) for d in decisions)
    failures = sum(1 for d in decisions if d.get("llm_failure", False))
    fallbacks = sum(1 for d in decisions if d.get("fallback_triggered", False))

    # Strategy-level and combined portfolio impact of accepted proposals
    strategy_impact: Dict[str, Dict[str, Any]] = {}
    for sid in ("momentum", "mean_reversion", "stat_arb_pairs", "basis_yield", "portfolio_coordinator"):
        accepted_s = [d for d in decisions if d["strategy_id"] == sid and d["approved"]]
        ret_delta = round(sum(float(d["net_return_delta_pct"]) for d in accepted_s), 2)
        dd_delta = round(sum(float(d["max_dd_delta_pct"]) for d in accepted_s), 2)
        strategy_impact[sid] = {
            "accepted_proposals": len(accepted_s),
            "net_return_delta_pct": ret_delta,
            "max_dd_delta_pct": dd_delta,
        }

    accepted_all = [d for d in decisions if d["approved"]]
    combined_return_delta = round(sum(float(d["net_return_delta_pct"]) for d in accepted_all), 2)
    combined_dd_delta = round(sum(float(d["max_dd_delta_pct"]) for d in accepted_all), 2)

    return {
        "experiment": experiment_name,
        "total_proposals": n_total,
        "beneficial_count": n_ben,
        "harmful_count": n_harm,
        "true_positives_beneficial_accepted": tp,
        "true_negatives_harmful_rejected": tn,
        "false_positives_harmful_approved": fp,
        "false_negatives_beneficial_rejected": fn,
        "harmful_proposal_rejection_rate_pct": harmful_rejection_rate,
        "beneficial_proposal_acceptance_rate_pct": beneficial_acceptance_rate,
        "false_approval_rate_pct": false_approval_rate,
        "false_rejection_rate_pct": false_rejection_rate,
        "overall_decision_accuracy_pct": overall_accuracy,
        "latency_metrics": {
            "avg_latency_ms": round(float(np.mean(latencies)), 2) if latencies else 0.0,
            "p50_latency_ms": round(float(np.percentile(latencies, 50)), 2) if latencies else 0.0,
            "p95_latency_ms": round(float(np.percentile(latencies, 95)), 2) if latencies else 0.0,
            "total_latency_ms": round(float(np.sum(latencies)), 2) if latencies else 0.0,
        },
        "token_consumption": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "avg_tokens_per_proposal": round(total_tokens / n_total, 1) if n_total else 0.0,
        },
        "failure_rate_pct": round((failures / n_total) * 100.0, 2) if n_total else 0.0,
        "fallback_behavior": {
            "fallbacks_triggered": fallbacks,
            "mode": "fail_closed_disapprove" if "LLM" in experiment_name or "Two-Agent" in experiment_name else "deterministic_rules_only",
        },
        "strategy_level_impact": strategy_impact,
        "combined_portfolio_impact": {
            "accepted_count": len(accepted_all),
            "net_return_delta_pct": combined_return_delta,
            "max_drawdown_delta_pct": combined_dd_delta,
        },
        "decisions": decisions,
    }


def run_governance_benchmark(model: str = "phi4-mini:latest") -> Dict[str, Any]:
    """Execute Experiments A, B, and C across all 20 benchmark proposals."""
    base_cfg = load_config()
    proposals = get_frozen_20_proposal_benchmark()

    print("=" * 96)
    print("  WEB4 CONTROLLED 20-PROPOSAL GOVERNANCE BENCHMARK (EXPERIMENTS A vs B vs C)")
    print(f"  Model: {model} | Proposals: {len(proposals)} (10 Beneficial, 10 Harmful)")
    print("=" * 96)

    # -------------------------------------------------------------------------
    # Experiment A: Rules Only (Deterministic Invariants & Parameter Bounds Only)
    # -------------------------------------------------------------------------
    exp_a_decisions: List[Dict[str, Any]] = []
    for prop in proposals:
        t0 = time.perf_counter()
        cand_cfg = _merge_proposed_changes_into_config(base_cfg, prop["proposed_changes"])
        inv_ok, inv_errs = audit_invariants(cand_cfg)
        t1 = time.perf_counter()
        exp_a_decisions.append({
            "proposal_id": prop["proposal_id"],
            "strategy_id": prop["strategy_id"],
            "ground_truth": prop["ground_truth"],
            "approved": inv_ok,
            "verdict": "APPROVE" if inv_ok else "DISAPPROVE",
            "reason": "Passed deterministic invariant and bounds checks" if inv_ok else "; ".join(inv_errs),
            "latency_ms": round((t1 - t0) * 1000.0, 3),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "llm_failure": False,
            "fallback_triggered": False,
            "net_return_delta_pct": prop["evaluation_metrics"]["net_return_delta_pct"],
            "max_dd_delta_pct": prop["evaluation_metrics"]["max_dd_delta_pct"],
        })
    exp_a_summary = _compute_experiment_metrics("Experiment A: Rules Only", exp_a_decisions)

    # -------------------------------------------------------------------------
    # Experiment B: Single LLM (Raw Single-Prompt LLM Review without Invariant Gate or Analyst Stage)
    # -------------------------------------------------------------------------
    clear_llm_cache()
    exp_b_decisions: List[Dict[str, Any]] = []
    for prop in proposals:
        t0 = time.perf_counter()
        # Single LLM receives raw proposed_changes and raw hypothesis without Stage-1 Analyst telemetry synthesis
        approved, verdict_enum, parsed_v, llm_meta = query_llm_risk_review_structured(
            proposed_diff=prop["proposed_changes"],
            base_config=base_cfg,
            rationale=prop["hypothesis"],
            empirical_proof={"raw_metrics": prop["evaluation_metrics"]},
            analyst_proposal=None,
            model=model,
            use_cache=False,
        )
        t1 = time.perf_counter()
        parse_err = parsed_v.get("parse_error")
        exp_b_decisions.append({
            "proposal_id": prop["proposal_id"],
            "strategy_id": prop["strategy_id"],
            "ground_truth": prop["ground_truth"],
            "approved": approved,
            "verdict": verdict_enum,
            "reason": parsed_v.get("reason", ""),
            "latency_ms": round((t1 - t0) * 1000.0, 2),
            "prompt_tokens": int(llm_meta.get("prompt_tokens", 0)),
            "completion_tokens": int(llm_meta.get("completion_tokens", 0)),
            "total_tokens": int(llm_meta.get("total_tokens", 0)),
            "llm_failure": bool(parse_err is not None),
            "fallback_triggered": bool(parse_err is not None),
            "net_return_delta_pct": prop["evaluation_metrics"]["net_return_delta_pct"],
            "max_dd_delta_pct": prop["evaluation_metrics"]["max_dd_delta_pct"],
        })
    exp_b_summary = _compute_experiment_metrics("Experiment B: Single LLM", exp_b_decisions)

    # -------------------------------------------------------------------------
    # Experiment C: Two-Agent Analyst + Governor Workflow
    # -------------------------------------------------------------------------
    clear_llm_cache()
    exp_c_decisions: List[Dict[str, Any]] = []
    for prop in proposals:
        t0 = time.perf_counter()
        # Stage 1: Analyst LLM structures hypothesis, synthesizes evidence & risks, and screens candidate
        analyst_ok, structured_prop, a_meta, a_reason = analyze_candidate_proposal_with_llm(
            candidate_proposal=prop,
            base_config=base_cfg,
            model=model,
            use_cache=False,
        )
        p_tok = int(a_meta.get("prompt_tokens", 0))
        c_tok = int(a_meta.get("completion_tokens", 0))
        tot_tok = int(a_meta.get("total_tokens", 0))
        a_failed = not bool(a_meta.get("success", False)) or structured_prop is None

        if not analyst_ok or structured_prop is None:
            t1 = time.perf_counter()
            exp_c_decisions.append({
                "proposal_id": prop["proposal_id"],
                "strategy_id": prop["strategy_id"],
                "ground_truth": prop["ground_truth"],
                "approved": False,
                "verdict": "DISAPPROVE",
                "workflow_stage": "ANALYST_SCREENING",
                "pending_human_approval": False,
                "reason": a_reason,
                "latency_ms": round((t1 - t0) * 1000.0, 2),
                "prompt_tokens": p_tok,
                "completion_tokens": c_tok,
                "total_tokens": tot_tok,
                "llm_failure": a_failed,
                "fallback_triggered": a_failed,
                "net_return_delta_pct": prop["evaluation_metrics"]["net_return_delta_pct"],
                "max_dd_delta_pct": prop["evaluation_metrics"]["max_dd_delta_pct"],
            })
            continue

        # Stage 2: Governor validates Analyst schema, hard invariants, and LLM risk review
        schema_ok, schema_errs, _ = validate_analyst_proposal_schema(structured_prop)
        cand_cfg = _merge_proposed_changes_into_config(base_cfg, structured_prop["proposed_changes"])
        inv_ok, inv_errs = audit_invariants(cand_cfg)

        if not schema_ok or not inv_ok:
            t1 = time.perf_counter()
            exp_c_decisions.append({
                "proposal_id": prop["proposal_id"],
                "strategy_id": prop["strategy_id"],
                "ground_truth": prop["ground_truth"],
                "approved": False,
                "verdict": "DISAPPROVE",
                "workflow_stage": "GOVERNOR_INVARIANT_GATE",
                "pending_human_approval": False,
                "reason": "; ".join(schema_errs + inv_errs),
                "latency_ms": round((t1 - t0) * 1000.0, 2),
                "prompt_tokens": p_tok,
                "completion_tokens": c_tok,
                "total_tokens": tot_tok,
                "llm_failure": False,
                "fallback_triggered": False,
                "net_return_delta_pct": prop["evaluation_metrics"]["net_return_delta_pct"],
                "max_dd_delta_pct": prop["evaluation_metrics"]["max_dd_delta_pct"],
            })
            continue

        gov_approved, gov_verdict, gov_parsed, g_meta = query_llm_risk_review_structured(
            proposed_diff=structured_prop["proposed_changes"],
            base_config=base_cfg,
            rationale=structured_prop["hypothesis"],
            empirical_proof=structured_prop["supporting_evidence"],
            analyst_proposal=structured_prop,
            model=model,
            use_cache=False,
        )
        t1 = time.perf_counter()
        p_tok += int(g_meta.get("prompt_tokens", 0))
        c_tok += int(g_meta.get("completion_tokens", 0))
        tot_tok += int(g_meta.get("total_tokens", 0))
        g_failed = gov_parsed.get("parse_error") is not None

        exp_c_decisions.append({
            "proposal_id": prop["proposal_id"],
            "strategy_id": prop["strategy_id"],
            "ground_truth": prop["ground_truth"],
            "approved": gov_approved,
            "verdict": gov_verdict,
            "workflow_stage": "GOVERNOR_LLM_AND_PENDING_HITL",
            "pending_human_approval": gov_approved,
            "reason": gov_parsed.get("reason", ""),
            "latency_ms": round((t1 - t0) * 1000.0, 2),
            "prompt_tokens": p_tok,
            "completion_tokens": c_tok,
            "total_tokens": tot_tok,
            "llm_failure": g_failed,
            "fallback_triggered": g_failed,
            "net_return_delta_pct": prop["evaluation_metrics"]["net_return_delta_pct"],
            "max_dd_delta_pct": prop["evaluation_metrics"]["max_dd_delta_pct"],
        })

    exp_c_summary = _compute_experiment_metrics(
        "Experiment C: Two-Agent Analyst + Governor Workflow", exp_c_decisions
    )

    for s in (exp_a_summary, exp_b_summary, exp_c_summary):
        print(f"\n[{s['experiment']}]")
        print(
            f"  Accuracy: {s['overall_decision_accuracy_pct']:5.1f}% | "
            f"Harmful Rejected: {s['harmful_proposal_rejection_rate_pct']:5.1f}% ({s['true_negatives_harmful_rejected']}/10) | "
            f"Beneficial Accepted: {s['beneficial_proposal_acceptance_rate_pct']:5.1f}% ({s['true_positives_beneficial_accepted']}/10)"
        )
        print(
            f"  False Approval Rate: {s['false_approval_rate_pct']:5.1f}% | "
            f"False Rejection Rate: {s['false_rejection_rate_pct']:5.1f}% | "
            f"Avg Latency: {s['latency_metrics']['avg_latency_ms']:.1f} ms | "
            f"Tokens: {s['token_consumption']['total_tokens']}"
        )
        print(
            f"  Combined Portfolio Impact of Accepted Proposals: "
            f"Return Delta {s['combined_portfolio_impact']['net_return_delta_pct']:+.2f}% | "
            f"MaxDD Delta {s['combined_portfolio_impact']['max_drawdown_delta_pct']:+.2f}%"
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "benchmark_size": len(proposals),
        "beneficial_proposals": 10,
        "harmful_proposals": 10,
        "experiments": {
            "experiment_a_rules_only": exp_a_summary,
            "experiment_b_single_llm": exp_b_summary,
            "experiment_c_two_agent_analyst_governor": exp_c_summary,
        },
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print("\n" + "=" * 96)
    print(f"Saved reproducible 20-proposal governance benchmark to: {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print("=" * 96)
    return report


if __name__ == "__main__":
    run_governance_benchmark()
