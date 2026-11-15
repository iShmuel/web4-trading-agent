"""Agent 10: Post-Trade Forensic Analyst Agent.

Analyzes closed trades and decision journals every X executions to provide
deep statistical diagnosis, regime attribution, and LLM-driven root-cause
reasoning with actionable optimization hypotheses.

Key capabilities:
1. Payoff & Expectancy Statistics: Win rate, profit factor, payoff ratio (avg win / avg loss).
2. Strategy & Regime Attribution: Momentum vs Mean Reversion, Trend vs Range Chop vs Macro Bear.
3. Loss Attribution: Analyzes why stop-losses or whipsaws occurred.
4. LLM Forensic Reasoning: Uses local Ollama (phi4-mini / qwen3) to evaluate losing trades
   and synthesize concrete mathematical parameter hypotheses.
5. Emits formal Trade Forensic Report for Agent 11 (Optimizer) and Agent 12 (Governor).
"""

import argparse
import json
import logging
import math
import re
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd

from agent.llm_client import query_local_llm, query_local_llm_with_metrics

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_FILE = PROJECT_ROOT / "data" / "paper_portfolio.json"
DECISION_JOURNAL_FILE = PROJECT_ROOT / "data" / "decision_journal.jsonl"
FORENSIC_REPORT_FILE = PROJECT_ROOT / "data" / "trade_forensic_report.json"

ANALYST_PROPOSAL_REQUIRED_FIELDS = {
    "proposal_id",
    "strategy_id",
    "strategy_name",
    "hypothesis",
    "proposed_changes",
    "supporting_evidence",
    "expected_benefit",
    "identified_risks",
    "evaluation_metrics",
    "confidence",
    "reasoning",
}

STRATEGY_NAMES = {
    "momentum": "Donchian Momentum Breakout",
    "mean_reversion": "Bollinger/RSI Mean Reversion",
    "stat_arb_pairs": "Cointegrated Statistical Arbitrage Pairs",
    "basis_yield": "Delta-Neutral Perpetual Basis Yield",
    "portfolio_coordinator": "Multi-Strategy Portfolio Coordinator",
}


@dataclass
class TradeForensicSummary:
    """Quantitative scorecard of evaluated trades."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    total_realized_pnl: float
    profit_factor: float
    avg_win_usd: float
    avg_loss_usd: float
    payoff_ratio: float
    expectancy_per_trade: float
    total_fees_paid: float
    momentum_trades: int
    momentum_pnl: float
    mean_reversion_trades: int
    mean_reversion_pnl: float
    top_winning_symbols: List[Dict[str, Any]]
    top_losing_symbols: List[Dict[str, Any]]
    losses_by_exit_reason: Dict[str, int]
    llm_forensic_diagnosis: str
    recommended_hypotheses: List[Dict[str, Any]]
    by_strategy: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    llm_metrics: Dict[str, Any] = field(default_factory=dict)
    fallback_used: bool = False


def validate_analyst_proposal_schema(
    proposal: Any,
) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """Validate an Analyst hypothesis proposal against the strict Web4 JSON schema.

    Required JSON schema:
    {
      "proposal_id": "...",
      "strategy_id": "...",
      "strategy_name": "...",
      "hypothesis": "...",
      "proposed_changes": {},
      "supporting_evidence": {},
      "expected_benefit": "...",
      "identified_risks": [],
      "evaluation_metrics": {},
      "confidence": 0.0,
      "reasoning": "..."
    }
    """
    if not isinstance(proposal, dict):
        return False, ["Proposal must be a JSON object (dict)."], None

    errors: List[str] = []
    missing = sorted(list(ANALYST_PROPOSAL_REQUIRED_FIELDS - set(proposal.keys())))
    if missing:
        errors.append(f"Missing required schema fields: {', '.join(missing)}")
        return False, errors, None

    str_fields = (
        "proposal_id",
        "strategy_id",
        "strategy_name",
        "hypothesis",
        "expected_benefit",
        "reasoning",
    )
    for sf in str_fields:
        val = proposal.get(sf)
        if not isinstance(val, str) or not val.strip():
            errors.append(f"Field '{sf}' must be a non-empty string.")

    dict_fields = ("proposed_changes", "supporting_evidence", "evaluation_metrics")
    for df_key in dict_fields:
        val = proposal.get(df_key)
        if not isinstance(val, dict):
            errors.append(f"Field '{df_key}' must be a JSON object (dict).")
        elif df_key == "proposed_changes" and len(val) == 0:
            errors.append("Field 'proposed_changes' cannot be empty.")

    risks = proposal.get("identified_risks")
    if not isinstance(risks, list) or len(risks) == 0 or not all(isinstance(r, str) and r.strip() for r in risks):
        errors.append("Field 'identified_risks' must be a non-empty list of strings.")

    try:
        conf = float(proposal.get("confidence"))
        if math.isnan(conf) or math.isinf(conf) or conf < 0.0 or conf > 1.0:
            errors.append("Field 'confidence' must be a finite float between 0.0 and 1.0.")
    except (TypeError, ValueError):
        errors.append("Field 'confidence' must be a numeric float.")
        conf = 0.0

    if errors:
        return False, errors, None

    normalized = {
        "proposal_id": str(proposal["proposal_id"]).strip(),
        "strategy_id": str(proposal["strategy_id"]).strip(),
        "strategy_name": str(proposal["strategy_name"]).strip(),
        "hypothesis": str(proposal["hypothesis"]).strip(),
        "proposed_changes": dict(proposal["proposed_changes"]),
        "supporting_evidence": dict(proposal["supporting_evidence"]),
        "expected_benefit": str(proposal["expected_benefit"]).strip(),
        "identified_risks": [str(r).strip() for r in proposal["identified_risks"]],
        "evaluation_metrics": dict(proposal["evaluation_metrics"]),
        "confidence": round(float(conf), 4),
        "reasoning": str(proposal["reasoning"]).strip(),
    }
    return True, [], normalized


def load_closed_trades(portfolio_path: Optional[Path] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Load closed trades from paper portfolio state."""
    path = portfolio_path or PORTFOLIO_FILE
    if not path.exists():
        return []
    try:
        with open(path, "r") as f:
            data = json.load(f)
        closed = data.get("closed_trades", [])
        if limit and limit > 0:
            return closed[-limit:]
        return closed
    except Exception as e:
        logger.error(f"[TradeAnalyst] Could not load portfolio trades: {e}")
        return []


def load_recent_decision_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Load the most recent settled decisions from decision_journal.jsonl."""
    if not DECISION_JOURNAL_FILE.exists():
        return []
    records = []
    try:
        with open(DECISION_JOURNAL_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        pass
        return records[-limit:]
    except Exception as e:
        logger.debug(f"[TradeAnalyst] Decision journal load notice: {e}")
        return []


def perform_statistical_audit(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute mathematical performance metrics across trade records."""
    if not trades:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate_pct": 0.0,
            "total_pnl": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "payoff_ratio": 0.0,
            "expectancy": 0.0,
            "total_fees": 0.0,
            "momentum_trades": 0,
            "momentum_pnl": 0.0,
            "mr_trades": 0,
            "mr_pnl": 0.0,
            "by_symbol": {},
            "exit_reasons": {},
        }

    pnls = [float(t.get("pnl", 0.0)) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p <= 0]

    gross_win = sum(wins)
    gross_loss = sum(losses)
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)

    total = len(trades)
    n_wins = len(wins)
    n_losses = len(losses)
    wr = round((n_wins / total) * 100.0, 1) if total > 0 else 0.0
    avg_win = round(float(np.mean(wins)), 2) if wins else 0.0
    avg_loss = round(float(np.mean(losses)), 2) if losses else 0.0
    payoff = round(avg_win / avg_loss, 2) if avg_loss > 0 else (1.0 if avg_win > 0 else 0.0)
    expectancy = round(float(np.mean(pnls)), 2) if pnls else 0.0
    total_fees = round(sum(float(t.get("fees", t.get("fees_paid", 0.0))) for t in trades), 2)

    # Strategy breakdown across all registered Web4 strategies
    mom_trades = [t for t in trades if t.get("strategy_id") == "momentum"]
    mr_trades = [t for t in trades if t.get("strategy_id") == "mean_reversion"]
    mom_pnl = round(sum(float(t.get("pnl", 0.0)) for t in mom_trades), 2)
    mr_pnl = round(sum(float(t.get("pnl", 0.0)) for t in mr_trades), 2)

    by_strategy: Dict[str, Dict[str, Any]] = {}
    for sid in ("momentum", "mean_reversion", "stat_arb_pairs", "basis_yield"):
        s_trades = [t for t in trades if t.get("strategy_id") == sid]
        s_pnls = [float(t.get("pnl", 0.0)) for t in s_trades]
        s_wins = [p for p in s_pnls if p > 0]
        s_losses = [abs(p) for p in s_pnls if p <= 0]
        s_gw, s_gl = sum(s_wins), sum(s_losses)
        by_strategy[sid] = {
            "strategy_name": STRATEGY_NAMES.get(sid, sid),
            "trades": len(s_trades),
            "wins": len(s_wins),
            "losses": len(s_losses),
            "win_rate_pct": round((len(s_wins) / len(s_trades)) * 100.0, 1) if s_trades else 0.0,
            "pnl": round(sum(s_pnls), 2),
            "profit_factor": round(s_gw / s_gl, 2) if s_gl > 0 else (99.0 if s_gw > 0 else 0.0),
            "expectancy": round(float(np.mean(s_pnls)), 2) if s_pnls else 0.0,
        }
    # Include any custom registered strategies present in trade records
    for t in trades:
        sid = str(t.get("strategy_id", "default"))
        if sid not in by_strategy:
            s_trades = [x for x in trades if x.get("strategy_id") == sid]
            s_pnls = [float(x.get("pnl", 0.0)) for x in s_trades]
            s_wins = [p for p in s_pnls if p > 0]
            by_strategy[sid] = {
                "strategy_name": STRATEGY_NAMES.get(sid, sid),
                "trades": len(s_trades),
                "wins": len(s_wins),
                "losses": len(s_trades) - len(s_wins),
                "win_rate_pct": round((len(s_wins) / len(s_trades)) * 100.0, 1) if s_trades else 0.0,
                "pnl": round(sum(s_pnls), 2),
                "expectancy": round(float(np.mean(s_pnls)), 2) if s_pnls else 0.0,
            }

    # Symbol breakdown
    by_symbol: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        sym = t.get("symbol", "UNKNOWN")
        p = float(t.get("pnl", 0.0))
        if sym not in by_symbol:
            by_symbol[sym] = {"trades": 0, "wins": 0, "pnl": 0.0}
        by_symbol[sym]["trades"] += 1
        if p > 0:
            by_symbol[sym]["wins"] += 1
        by_symbol[sym]["pnl"] += p

    for s, data in by_symbol.items():
        data["pnl"] = round(data["pnl"], 2)
        data["win_rate"] = round((data["wins"] / data["trades"]) * 100.0, 1)

    # Exit reason breakdown for losing trades
    exit_reasons: Dict[str, int] = {}
    for t in trades:
        if float(t.get("pnl", 0.0)) <= 0:
            r = t.get("exit_reason", "Unspecified Stop")
            exit_reasons[r] = exit_reasons.get(r, 0) + 1

    return {
        "total_trades": total,
        "winning_trades": n_wins,
        "losing_trades": n_losses,
        "win_rate_pct": wr,
        "total_pnl": round(sum(pnls), 2),
        "profit_factor": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff,
        "expectancy": expectancy,
        "total_fees": total_fees,
        "momentum_trades": len(mom_trades),
        "momentum_pnl": mom_pnl,
        "mr_trades": len(mr_trades),
        "mr_pnl": mr_pnl,
        "by_strategy": by_strategy,
        "by_symbol": by_symbol,
        "exit_reasons": exit_reasons,
    }


def build_deterministic_analyst_proposals(stats: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build schema-compliant deterministic fallback proposals when LLM is disabled or unreachable."""
    proposals: List[Dict[str, Any]] = []
    by_strat = stats.get("by_strategy", {})
    mom_info = by_strat.get("momentum", {})

    has_mom_trades = int(stats.get("momentum_trades", mom_info.get("trades", 0)) or 0) > 0 or not by_strat
    if has_mom_trades and (stats.get("win_rate_pct", 0.0) < 55.0 or float(mom_info.get("pnl", 0.0)) < 0.0):
        proposals.append({
            "proposal_id": f"PROP-RULE-{uuid.uuid4().hex[:6].upper()}",
            "strategy_id": "momentum",
            "strategy_name": STRATEGY_NAMES["momentum"],
            "hypothesis": "Elevating ADX chop threshold from 20.0 to 22.0 will filter low-momentum range whipsaws.",
            "proposed_changes": {"adx_chop_threshold": 22.0},
            "supporting_evidence": {
                "overall_win_rate_pct": stats.get("win_rate_pct", 0.0),
                "momentum_pnl": stats.get("momentum_pnl", 0.0),
                "momentum_trades": stats.get("momentum_trades", 0),
            },
            "expected_benefit": "Reduce false breakout entries in sideways consolidation and improve profit factor.",
            "identified_risks": [
                "Slightly delayed entry on early trend transitions when ADX is between 20 and 22."
            ],
            "evaluation_metrics": {
                "target_win_rate_pct": 55.0,
                "target_profit_factor": 1.15,
            },
            "confidence": 0.72,
            "reasoning": "Deterministic rule fallback: Momentum losses cluster during low-ADX range chop.",
            "target": "global_regime",
            "parameter": "adx_chop_threshold",
            "suggested_value": 22.0,
            "rationale": "Elevate ADX chop threshold from 20 to 22 to block low-volatility whipsaw entries.",
        })

    sym_items = sorted(list(stats.get("by_symbol", {}).items()), key=lambda x: x[1]["pnl"], reverse=True)
    if sym_items and sym_items[-1][1]["pnl"] < -50.0:
        worst_sym, worst_data = sym_items[-1]
        proposals.append({
            "proposal_id": f"PROP-RULE-{uuid.uuid4().hex[:6].upper()}",
            "strategy_id": "portfolio_coordinator",
            "strategy_name": STRATEGY_NAMES["portfolio_coordinator"],
            "hypothesis": f"Blacklisting persistent underperformer {worst_sym} will eliminate tail drag.",
            "proposed_changes": {"blacklisted_symbols": [worst_sym]},
            "supporting_evidence": {
                "symbol": worst_sym,
                "realized_pnl_usd": worst_data["pnl"],
                "win_rate_pct": worst_data["win_rate"],
                "trades": worst_data["trades"],
            },
            "expected_benefit": f"Eliminate ${abs(worst_data['pnl']):.2f} realized loss drag from active rotation.",
            "identified_risks": [
                f"Opportunity cost if {worst_sym} enters a clean breakout regime before rehabilitation."
            ],
            "evaluation_metrics": {
                "symbol_pnl_threshold_usd": -50.0,
                "observed_pnl_usd": worst_data["pnl"],
            },
            "confidence": 0.80,
            "reasoning": f"Deterministic rule fallback: {worst_sym} breached the -$50.00 cumulative loss threshold.",
            "target": "asset_pruning",
            "parameter": "blacklist_symbol",
            "suggested_value": worst_sym,
            "rationale": f"Prune {worst_sym} due to net loss drag of ${worst_data['pnl']:.2f}.",
        })

    mr_info = by_strat.get("mean_reversion", {})
    if int(mr_info.get("trades", 0)) > 0 and float(mr_info.get("pnl", 0.0)) < 0.0:
        proposals.append({
            "proposal_id": f"PROP-RULE-{uuid.uuid4().hex[:6].upper()}",
            "strategy_id": "mean_reversion",
            "strategy_name": STRATEGY_NAMES["mean_reversion"],
            "hypothesis": "Widening Bollinger Band entry dispersion to 2.1 std will filter premature counter-trend entries.",
            "proposed_changes": {"bb_std": 2.1},
            "supporting_evidence": {
                "mr_trades": mr_info.get("trades", 0),
                "mr_pnl": mr_info.get("pnl", 0.0),
                "mr_win_rate_pct": mr_info.get("win_rate_pct", 0.0),
            },
            "expected_benefit": "Improve Mean Reversion win rate and avoid catching falling knives before exhaustion.",
            "identified_risks": [
                "Fewer mean-reversion signals during mild consolidation bands."
            ],
            "evaluation_metrics": {
                "target_win_rate_pct": 58.0,
                "target_profit_factor": 1.25,
            },
            "confidence": 0.75,
            "reasoning": "Deterministic rule fallback: Mean Reversion realized negative net PnL.",
            "target": "mean_reversion",
            "parameter": "bb_std",
            "suggested_value": 2.1,
            "rationale": "Widen Bollinger Band entry standard deviation to 2.1 to require deeper exhaustion.",
        })

    pairs_info = by_strat.get("stat_arb_pairs", {})
    if int(pairs_info.get("trades", 0)) > 0 and float(pairs_info.get("pnl", 0.0)) < 0.0:
        proposals.append({
            "proposal_id": f"PROP-RULE-{uuid.uuid4().hex[:6].upper()}",
            "strategy_id": "stat_arb_pairs",
            "strategy_name": STRATEGY_NAMES["stat_arb_pairs"],
            "hypothesis": "Raising Stat-Arb entry Z-score threshold to 2.75 will ensure wider spread dispersion before entry.",
            "proposed_changes": {"stat_arb_entry_z": 2.75},
            "supporting_evidence": {
                "pairs_trades": pairs_info.get("trades", 0),
                "pairs_pnl": pairs_info.get("pnl", 0.0),
                "pairs_win_rate_pct": pairs_info.get("win_rate_pct", 0.0),
            },
            "expected_benefit": "Increase expected spread reversion edge relative to two-leg round-trip friction.",
            "identified_risks": [
                "Reduced pair trade frequency when spread oscillations remain below 2.75 sigma."
            ],
            "evaluation_metrics": {
                "target_win_rate_pct": 60.0,
                "target_profit_factor": 1.30,
            },
            "confidence": 0.76,
            "reasoning": "Deterministic rule fallback: Stat-Arb pairs realized negative net PnL.",
            "target": "stat_arb_pairs",
            "parameter": "stat_arb_entry_z",
            "suggested_value": 2.75,
            "rationale": "Raise Stat-Arb entry Z-score to 2.75 to require wider spread dislocation.",
        })

    basis_info = by_strat.get("basis_yield", {})
    if int(basis_info.get("trades", 0)) > 0 and float(basis_info.get("pnl", 0.0)) < 0.0:
        proposals.append({
            "proposal_id": f"PROP-RULE-{uuid.uuid4().hex[:6].upper()}",
            "strategy_id": "basis_yield",
            "strategy_name": STRATEGY_NAMES["basis_yield"],
            "hypothesis": "Raising basis hurdle APR to 10.0% will ensure funding carry exceeds hedging friction.",
            "proposed_changes": {"basis_hurdle_apr": 10.0},
            "supporting_evidence": {
                "basis_trades": basis_info.get("trades", 0),
                "basis_pnl": basis_info.get("pnl", 0.0),
            },
            "expected_benefit": "Prevent low-yield carry allocations from being eroded by rebalance slippage.",
            "identified_risks": [
                "Idle cash remains unallocated when annualized funding rates are between 8% and 10%."
            ],
            "evaluation_metrics": {
                "target_profit_factor": 1.50,
            },
            "confidence": 0.75,
            "reasoning": "Deterministic rule fallback: Basis yield realized negative net PnL.",
            "target": "basis_yield",
            "parameter": "basis_hurdle_apr",
            "suggested_value": 10.0,
            "rationale": "Raise basis hurdle APR to 10.0% to cover delta-hedging costs.",
        })

    return proposals


def generate_llm_analyst_proposals(
    stats: Dict[str, Any],
    sample_losses: List[Dict[str, Any]],
    model: str = "phi4-mini:latest",
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Use the local LLM to analyze multi-strategy trade forensics and generate schema-validated JSON proposals."""
    loss_summary = [
        {
            "symbol": t.get("symbol"),
            "strategy_id": t.get("strategy_id"),
            "pnl": t.get("pnl"),
            "return_pct": t.get("return_pct", 0.0),
            "exit_reason": t.get("exit_reason"),
        }
        for t in sample_losses[:6]
    ]

    prompt = f"""You are Agent 10, the Quantitative Trade Forensic Analyst for the Web4 Multi-Strategy Trading System.
Inspect the following empirical performance telemetry across our registered strategies (momentum, mean_reversion, stat_arb_pairs, basis_yield) and generate structured, testable parameter improvement hypotheses.

[PORTFOLIO & STRATEGY TELEMETRY]
{json.dumps({
    "total_trades": stats.get("total_trades", 0),
    "win_rate_pct": stats.get("win_rate_pct", 0.0),
    "total_pnl": stats.get("total_pnl", 0.0),
    "profit_factor": stats.get("profit_factor", 0.0),
    "payoff_ratio": stats.get("payoff_ratio", 0.0),
    "total_fees": stats.get("total_fees", 0.0),
    "by_strategy": stats.get("by_strategy", {}),
    "by_symbol": stats.get("by_symbol", {}),
    "exit_reasons": stats.get("exit_reasons", {}),
    "sample_losses": loss_summary,
}, indent=2)}

[SAFE PARAMETER BOUNDS]
- account_risk_pct: [0.01, 0.05] (1%-5% hard invariant)
- adx_chop_threshold: [12.0, 32.0], adx_trend_threshold: [18.0, 45.0]
- breakout_lookback: [6, 168], atr_multiplier: [1.0, 5.0]
- rsi_oversold: [15.0, 45.0], rsi_overbought: [55.0, 85.0]
- stat_arb_z_entry: [1.2, 4.0], basis_hurdle_apr: [2.0, 40.0]

Output ONLY a valid JSON object matching this exact structure:
{{
  "diagnosis": "2-3 sentence root-cause forensic diagnosis across strategies and regimes.",
  "proposals": [
    {{
      "proposal_id": "PROP-LLM-001",
      "strategy_id": "momentum | mean_reversion | stat_arb_pairs | basis_yield | portfolio_coordinator",
      "strategy_name": "Human-readable strategy name",
      "hypothesis": "Clear, falsifiable quantitative hypothesis",
      "proposed_changes": {{"adx_chop_threshold": 22.0}},
      "supporting_evidence": {{"win_rate_pct": {stats.get('win_rate_pct', 0.0)}, "profit_factor": {stats.get('profit_factor', 0.0)}}},
      "expected_benefit": "Expected improvement in expectancy, win rate, or drawdown control",
      "identified_risks": ["Specific trade-off or regime risk of applying this change"],
      "evaluation_metrics": {{"target_profit_factor": 1.2, "max_drawdown_cap_pct": 12.0}},
      "confidence": 0.78,
      "reasoning": "Step-by-step quantitative reasoning connecting trade losses to the proposed parameter change"
    }}
  ]
}}"""

    system_prompt = (
        "You are an institutional quantitative trade analyst. Respond ONLY with valid JSON containing "
        "'diagnosis' (string) and 'proposals' (array of structured proposal objects with all 11 required fields: "
        "proposal_id, strategy_id, strategy_name, hypothesis, proposed_changes, supporting_evidence, "
        "expected_benefit, identified_risks, evaluation_metrics, confidence, reasoning)."
    )

    llm_call = query_local_llm_with_metrics(
        prompt=prompt,
        system_prompt=system_prompt,
        model=model,
        temperature=0.2,
        max_tokens=500,
        timeout_seconds=18.0,
        use_cache=use_cache,
        json_mode=True,
    )

    raw_text = llm_call.get("response")
    if not llm_call.get("success") or not raw_text:
        return {
            "diagnosis": "",
            "proposals": [],
            "schema_valid": False,
            "schema_errors": [llm_call.get("error") or "Empty LLM response"],
            "llm_metrics": llm_call,
            "fallback_used": True,
        }

    text = raw_text.strip()
    if text.startswith("```"):
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        if m:
            text = m.group(1).strip()

    try:
        parsed = json.loads(text)
    except Exception as e:
        return {
            "diagnosis": "",
            "proposals": [],
            "schema_valid": False,
            "schema_errors": [f"Malformed JSON from Analyst LLM: {e}"],
            "llm_metrics": llm_call,
            "fallback_used": True,
        }

    if not isinstance(parsed, dict):
        return {
            "diagnosis": "",
            "proposals": [],
            "schema_valid": False,
            "schema_errors": ["Root JSON from Analyst LLM must be an object."],
            "llm_metrics": llm_call,
            "fallback_used": True,
        }

    diagnosis_str = str(parsed.get("diagnosis", "")).strip()
    raw_proposals = parsed.get("proposals", [])
    if isinstance(parsed, dict) and "proposal_id" in parsed and not raw_proposals:
        raw_proposals = [parsed]

    validated_proposals: List[Dict[str, Any]] = []
    schema_errors: List[str] = []

    if not isinstance(raw_proposals, list):
        schema_errors.append("'proposals' field must be a list.")
    else:
        for idx, item in enumerate(raw_proposals):
            ok, errs, norm = validate_analyst_proposal_schema(item)
            if ok and norm is not None:
                # Populate legacy display keys for backward CLI compatibility
                first_k = next(iter(norm["proposed_changes"].keys()), "parameter")
                first_v = norm["proposed_changes"][first_k]
                norm.setdefault("target", norm["strategy_id"])
                norm.setdefault("parameter", first_k)
                norm.setdefault("suggested_value", first_v)
                norm.setdefault("rationale", norm["hypothesis"])
                validated_proposals.append(norm)
            else:
                schema_errors.extend([f"proposals[{idx}]: {err}" for err in errs])

    return {
        "diagnosis": diagnosis_str,
        "proposals": validated_proposals,
        "schema_valid": len(validated_proposals) > 0 and len(schema_errors) == 0,
        "schema_errors": schema_errors,
        "llm_metrics": llm_call,
        "fallback_used": len(validated_proposals) == 0,
    }


def query_llm_trade_forensics(
    stats: Dict[str, Any],
    sample_losses: List[Dict[str, Any]],
    model: str = "phi4-mini:latest",
) -> Optional[str]:
    """Invoke local Ollama LLM to synthesize forensic diagnosis and structured hypotheses."""
    res = generate_llm_analyst_proposals(stats, sample_losses, model=model)
    if res.get("diagnosis"):
        return res["diagnosis"]
    return None


def analyze_candidate_proposal_with_llm(
    candidate_proposal: Dict[str, Any],
    base_config: Dict[str, Any],
    model: str = "phi4-mini:latest",
    use_cache: bool = True,
) -> Tuple[bool, Optional[Dict[str, Any]], Dict[str, Any], str]:
    """Analyst Stage 1 evaluation in the Two-Agent (Analyst -> Governor) workflow.

    The Analyst inspects a raw or candidate strategy proposal, evaluates its supporting
    evidence, expected benefit, and risks across the Web4 strategies, and outputs:
    - analyst_recommend_submit: bool (True if Analyst endorses forwarding to Governor, False if Analyst filters it out)
    - structured_proposal: Dict[str, Any] conforming to the 11-field Analyst JSON schema
    - llm_metrics: Dict[str, Any] (latency, tokens, success)
    - reason: str
    """
    ev = candidate_proposal.get("supporting_evidence", {}) or {}
    eval_m = candidate_proposal.get("evaluation_metrics", {}) or {}
    ret_delta = float(ev.get("replay_return_delta_pct", eval_m.get("net_return_delta_pct", 0.0)))
    dd_delta = float(ev.get("replay_max_dd_delta_pct", eval_m.get("max_dd_delta_pct", 0.0)))
    sharpe_delta = float(ev.get("replay_sharpe_delta", 0.0))
    pf_after = float(ev.get("profit_factor_after", 1.1))

    telemetry_digest = {
        "return_change_interpretation": (
            f"POSITIVE (+{ret_delta:.2f}% net return improvement)"
            if ret_delta > 0
            else f"NEGATIVE ({ret_delta:.2f}% net return degradation)"
        ),
        "drawdown_change_interpretation": (
            f"IMPROVED (Max drawdown reduced by {abs(dd_delta):.2f}%)"
            if dd_delta <= 0
            else f"WORSENED (Max drawdown increased by +{dd_delta:.2f}%)"
        ),
        "sharpe_change": f"{sharpe_delta:+.2f}",
        "profit_factor_after": pf_after,
    }

    prompt = f"""You are Agent 10, the Quantitative Strategy Analyst in a two-agent (Analyst -> Governor) workflow.
Your job is to formulate a rigorous structured hypothesis object and decide whether this candidate configuration change should be submitted to the Chief Risk Governor or filtered out immediately as harmful/unjustified.

[CANDIDATE PROPOSAL INPUT]
{json.dumps(candidate_proposal, indent=2)}

[QUANTITATIVE TELEMETRY INTERPRETATION (SIGN CONVENTION VERIFIED)]
{json.dumps(telemetry_digest, indent=2)}

[ACTIVE SYSTEM CONFIGURATION SUMMARY]
{json.dumps({
    "execution_mode": base_config.get("execution_mode", "maker"),
    "account_risk_pct": base_config.get("account_risk_pct", 0.02),
    "max_drawdown_limit_pct": base_config.get("max_drawdown_limit_pct", 15.0),
    "adx_chop_threshold": base_config.get("adx_chop_threshold", 20.0),
    "adx_trend_threshold": base_config.get("adx_trend_threshold", 25.0),
    "breakout_lookback": base_config.get("breakout_lookback", 24),
    "atr_multiplier": base_config.get("atr_multiplier", 2.5),
}, indent=2)}

[ANALYST SCREENING RULES]
- Recommend "SUBMIT_TO_GOVERNOR" when the proposed_changes are within safe parameter bounds AND the Quantitative Telemetry Interpretation shows POSITIVE net return improvement and IMPROVED (reduced) maximum drawdown. Note: every real trading parameter adjustment has normal trade-offs (which you should list in 'identified_risks'), so do NOT reject a proposal merely because trade-offs exist when its net return and drawdown telemetry are positive!
- Recommend "REJECT_AT_ANALYST" when the proposal violates risk bounds, disables safety gates (e.g. enable_regime_filter=False), uses extreme/noisy parameters (e.g. breakout_lookback <= 8, stat_arb_z_entry <= 1.3, basis_hurdle_apr <= 3.0, bb_std <= 1.3), OR has NEGATIVE return degradation / WORSENED drawdown.

Output ONLY a valid JSON object with this exact schema:
{{
  "analyst_decision": "SUBMIT_TO_GOVERNOR" | "REJECT_AT_ANALYST",
  "proposal": {{
    "proposal_id": "{candidate_proposal.get('proposal_id', 'PROP-001')}",
    "strategy_id": "{candidate_proposal.get('strategy_id', 'momentum')}",
    "strategy_name": "{candidate_proposal.get('strategy_name', STRATEGY_NAMES.get(candidate_proposal.get('strategy_id', 'momentum'), 'Web4 Strategy'))}",
    "hypothesis": "Falsifiable quantitative hypothesis statement",
    "proposed_changes": {json.dumps(candidate_proposal.get('proposed_changes', {}))},
    "supporting_evidence": {json.dumps(candidate_proposal.get('supporting_evidence', {}))},
    "expected_benefit": "Expected impact on strategy and portfolio metrics",
    "identified_risks": ["Risk 1", "Risk 2"],
    "evaluation_metrics": {json.dumps(candidate_proposal.get('evaluation_metrics', {}))},
    "confidence": 0.82,
    "reasoning": "Detailed analytical reasoning for submitting or rejecting this hypothesis"
  }}
}}"""

    system_prompt = (
        "You are a quantitative strategy analyst. Output ONLY valid JSON containing 'analyst_decision' "
        "('SUBMIT_TO_GOVERNOR' or 'REJECT_AT_ANALYST') and 'proposal' (containing all 11 required schema fields)."
    )

    llm_call = query_local_llm_with_metrics(
        prompt=prompt,
        system_prompt=system_prompt,
        model=model,
        temperature=0.1,
        max_tokens=450,
        timeout_seconds=18.0,
        use_cache=use_cache,
        json_mode=True,
    )

    if not llm_call.get("success") or not llm_call.get("response"):
        return False, None, llm_call, f"Fail-closed at Analyst: {llm_call.get('error') or 'Empty LLM response'}"

    text = llm_call["response"].strip()
    if text.startswith("```"):
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        if m:
            text = m.group(1).strip()

    try:
        parsed = json.loads(text)
    except Exception as e:
        return False, None, llm_call, f"Fail-closed at Analyst: Malformed JSON ({e})"

    if not isinstance(parsed, dict):
        return False, None, llm_call, "Fail-closed at Analyst: Root JSON must be an object"

    decision_str = str(parsed.get("analyst_decision", "")).strip().upper()
    prop_obj = parsed.get("proposal")
    if not isinstance(prop_obj, dict):
        return False, None, llm_call, "Fail-closed at Analyst: Missing 'proposal' object"

    # Ensure immutable fields from candidate input are preserved accurately in the structured proposal
    prop_obj["proposal_id"] = str(candidate_proposal.get("proposal_id", prop_obj.get("proposal_id", "PROP-001")))
    prop_obj["strategy_id"] = str(candidate_proposal.get("strategy_id", prop_obj.get("strategy_id", "momentum")))
    prop_obj["strategy_name"] = str(
        candidate_proposal.get(
            "strategy_name",
            prop_obj.get("strategy_name", STRATEGY_NAMES.get(prop_obj["strategy_id"], prop_obj["strategy_id"])),
        )
    )
    prop_obj["proposed_changes"] = dict(candidate_proposal.get("proposed_changes", prop_obj.get("proposed_changes", {})))
    if not prop_obj.get("supporting_evidence") and candidate_proposal.get("supporting_evidence"):
        prop_obj["supporting_evidence"] = dict(candidate_proposal["supporting_evidence"])
    if not prop_obj.get("evaluation_metrics") and candidate_proposal.get("evaluation_metrics"):
        prop_obj["evaluation_metrics"] = dict(candidate_proposal["evaluation_metrics"])

    schema_ok, schema_errs, norm_prop = validate_analyst_proposal_schema(prop_obj)
    if not schema_ok or norm_prop is None:
        return False, None, llm_call, f"Fail-closed at Analyst: Schema validation failed ({'; '.join(schema_errs)})"

    if decision_str != "SUBMIT_TO_GOVERNOR":
        return False, norm_prop, llm_call, f"Rejected by Analyst ({decision_str}): {norm_prop['reasoning']}"

    return True, norm_prop, llm_call, norm_prop["reasoning"]


def run_trade_forensic_analysis(
    trade_window: int = 15,  # Mathematically optimal deal volume: 15-20 trades
    portfolio_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
    use_llm: bool = True,
    model: str = "phi4-mini:latest",
    auto_submit_to_governor: bool = False,
) -> TradeForensicSummary:
    """Execute complete post-trade forensic analysis cycle."""
    trades = load_closed_trades(portfolio_path=portfolio_path, limit=trade_window)
    stats = perform_statistical_audit(trades)

    # Isolate top winners and losers
    sym_items = list(stats.get("by_symbol", {}).items())
    sorted_syms = sorted(sym_items, key=lambda x: x[1]["pnl"], reverse=True)
    top_winners = [{"symbol": s, **d} for s, d in sorted_syms if d["pnl"] > 0][:5]
    top_losers = [{"symbol": s, **d} for s, d in sorted_syms if d["pnl"] < 0][:5]

    sample_losses = [t for t in trades if float(t.get("pnl", 0.0)) < 0]

    # LLM Forensic Reasoning & Structured Hypothesis Generation
    llm_diagnosis = ""
    hypotheses: List[Dict[str, Any]] = []
    llm_metrics: Dict[str, Any] = {}
    fallback_used = False

    if use_llm and trades:
        llm_res = generate_llm_analyst_proposals(stats, sample_losses, model=model)
        llm_diagnosis = llm_res.get("diagnosis", "")
        hypotheses = llm_res.get("proposals", [])
        llm_metrics = llm_res.get("llm_metrics", {})
        fallback_used = bool(llm_res.get("fallback_used", False))

    if not llm_diagnosis:
        fallback_used = True
        if stats["win_rate_pct"] >= 60.0:
            llm_diagnosis = f"Desk is performing with strong statistical edge ({stats['win_rate_pct']}% Win Rate, PF {stats['profit_factor']}). Primary drag stems from trailing stop slippage."
        elif stats["win_rate_pct"] >= 50.0:
            llm_diagnosis = f"Desk is breaking even in choppy consolidation ({stats['win_rate_pct']}% Win Rate). Increasing ADX chop thresholds is recommended."
        else:
            llm_diagnosis = f"Desk experiencing negative expectancy ({stats['win_rate_pct']}% Win Rate). Pruning underperforming drag symbols is recommended."

    if not hypotheses:
        fallback_used = True
        hypotheses = build_deterministic_analyst_proposals(stats)

    summary = TradeForensicSummary(
        total_trades=stats["total_trades"],
        winning_trades=stats["winning_trades"],
        losing_trades=stats["losing_trades"],
        win_rate_pct=stats["win_rate_pct"],
        total_realized_pnl=stats["total_pnl"],
        profit_factor=stats["profit_factor"],
        avg_win_usd=stats["avg_win"],
        avg_loss_usd=stats["avg_loss"],
        payoff_ratio=stats["payoff_ratio"],
        expectancy_per_trade=stats["expectancy"],
        total_fees_paid=stats["total_fees"],
        momentum_trades=stats["momentum_trades"],
        momentum_pnl=stats["momentum_pnl"],
        mean_reversion_trades=stats["mr_trades"],
        mean_reversion_pnl=stats["mr_pnl"],
        top_winning_symbols=top_winners,
        top_losing_symbols=top_losers,
        losses_by_exit_reason=stats.get("exit_reasons", {}),
        llm_forensic_diagnosis=llm_diagnosis,
        recommended_hypotheses=hypotheses,
        by_strategy=stats.get("by_strategy", {}),
        llm_metrics=llm_metrics,
        fallback_used=fallback_used,
    )

    out = output_path or FORENSIC_REPORT_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(asdict(summary), f, indent=2)

    # Automated governance submission if requested (saves to Pending Human Approval queue)
    if auto_submit_to_governor and hypotheses:
        try:
            from agent.governor import review_and_deploy_proposal
            for h in hypotheses:
                ok_schema, _, norm_prop = validate_analyst_proposal_schema(h)
                if not ok_schema or norm_prop is None:
                    continue
                prop_diff = norm_prop["proposed_changes"]
                print(
                    f"[TradeAnalyst] Submitting structured hypothesis {norm_prop['proposal_id']} "
                    f"({norm_prop['strategy_id']}) to Agent 12 Governor: {prop_diff}"
                )
                review_and_deploy_proposal(
                    proposed_changes=prop_diff,
                    rationale=norm_prop["hypothesis"],
                    empirical_proof=norm_prop["supporting_evidence"],
                    verify_tests=True,
                    use_llm=use_llm,
                    model=model,
                    require_human_approval=True,
                    human_approved=False,
                    analyst_proposal=norm_prop,
                    proposal_id=norm_prop["proposal_id"],
                )
        except Exception as e:
            logger.error(f"[TradeAnalyst] Governance submission notice: {e}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Agent 10: Post-Trade Forensic Analyst")
    parser.add_argument("--trades", type=int, default=25, help="Number of recent closed trades to analyze (default: 25)")
    parser.add_argument("--portfolio", type=str, default=None, help="Custom portfolio json path")
    parser.add_argument("--report", type=str, default=None, help="Output forensic report path")
    parser.add_argument("--no-llm", action="store_true", help="Bypass LLM query (pure mathematical audit)")
    parser.add_argument("--model", type=str, default="phi4-mini:latest", help="Local Ollama model (default: phi4-mini:latest)")
    args = parser.parse_args()

    port_path = Path(args.portfolio) if args.portfolio else PORTFOLIO_FILE
    rep_path = Path(args.report) if args.report else FORENSIC_REPORT_FILE

    print("=" * 80)
    print(f"  AGENT 10: POST-TRADE FORENSIC ANALYST AGENT")
    print(f"  Analyzing last {args.trades} closed trades from: {port_path.name}")
    print("=" * 80)

    summary = run_trade_forensic_analysis(
        trade_window=args.trades,
        portfolio_path=port_path,
        output_path=rep_path,
        use_llm=not args.no_llm,
        model=args.model,
    )

    print(f"\n[Trade Scorecard]")
    print(f"  Total Trades:     {summary.total_trades} ({summary.winning_trades} Wins / {summary.losing_trades} Losses)")
    print(f"  Win Rate:         {summary.win_rate_pct}%")
    print(f"  Realized PnL:     ${summary.total_realized_pnl:+,.2f}")
    print(f"  Profit Factor:    {summary.profit_factor:.2f}")
    print(f"  Payoff Ratio:     {summary.payoff_ratio:.2f}x (Avg Win: ${summary.avg_win_usd:+.2f} / Avg Loss: ${summary.avg_loss_usd:+.2f})")
    print(f"  Expectancy:       ${summary.expectancy_per_trade:+.2f} per trade")
    print(f"  Total Fees Paid:  ${summary.total_fees_paid:,.2f}")
    print(f"  Momentum PnL:     ${summary.momentum_pnl:+,.2f} ({summary.momentum_trades} trades)")
    print(f"  Mean-Rev PnL:     ${summary.mean_reversion_pnl:+,.2f} ({summary.mean_reversion_trades} trades)")

    win_labels = [f"{w['symbol']} (${w['pnl']:+,.1f})" for w in summary.top_winning_symbols]
    lose_labels = [f"{l['symbol']} (${l['pnl']:+,.1f})" for l in summary.top_losing_symbols]
    print(f"\n[Top Contributors]")
    print(f"  Winners: {win_labels}")
    print(f"  Drags:   {lose_labels}")

    print(f"\n[Losses by Exit Reason]")
    for r, cnt in summary.losses_by_exit_reason.items():
        print(f"  - {r}: {cnt} trades")

    print(f"\n[LLM Forensic Reasoning & Diagnosis]")
    print(summary.llm_forensic_diagnosis)

    print(f"\n[Actionable Hypotheses Generated for Agent 11 & 12]")
    for h in summary.recommended_hypotheses:
        print(f"  → [{h['target']}] Set {h['parameter']} = {h['suggested_value']} ({h['rationale']})")

    print("=" * 80)
    print(f"Report saved to: {rep_path}\n")


if __name__ == "__main__":
    main()
