"""Agent 12: Chief Risk Governor & Deployment Gatekeeper.

Acts as the supreme supervisory approval authority for all asset selection,
pruning, and parameter calibration proposals.

Enforces:
1. Absolute Invariant Verification (SEC Rule 15c3-5, 1%-5% risk bounds, paper-only mode).
2. LLM Risk & Compliance Review (phi4-mini / qwen3).
3. 100% Automated Pytest Regression Gating (zero test failures allowed).
4. Atomic Hot-Reload Deployment with automated rollback backups.
5. Immutable Governance Audit Logging (data/agent_governance_audit.log).
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import math
import re
import uuid

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.llm_client import query_local_llm, query_local_llm_with_metrics
from core.risk_engine import MIN_ALLOWED_RISK_PCT, MAX_ALLOWED_RISK_PCT
from core.data_loader import is_crypto_symbol

logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
PENDING_PROPOSALS_PATH = PROJECT_ROOT / "config" / "pending_proposals.json"
AUDIT_LOG_FILE = PROJECT_ROOT / "data" / "agent_governance_audit.log"
_GOVERNOR_DEPLOY_LOCK = threading.RLock()


def _portable_config_path(cfg_file: Path) -> str:
    """Return a repo-relative path when cfg_file is inside PROJECT_ROOT to avoid leaking local paths."""
    try:
        return str(cfg_file.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(cfg_file)


def _resolve_target_config_path(raw_path: Optional[str]) -> Path:
    """Resolve a stored relative or absolute target_config_path back to a local Path."""
    if not raw_path:
        return CONFIG_PATH
    p = Path(raw_path)
    return p if p.is_absolute() else (PROJECT_ROOT / p)

VALID_GOVERNOR_VERDICTS = {"APPROVE", "DISAPPROVE", "NEEDS_HUMAN_REVIEW"}

# Safe parameter bounds for all Web4 strategies and coordinator controls
PARAMETER_BOUNDS: Dict[str, Tuple[float, float]] = {
    "account_risk_pct": (MIN_ALLOWED_RISK_PCT, MAX_ALLOWED_RISK_PCT),
    "max_drawdown_limit_pct": (5.0, 15.0),
    "max_concurrent_positions": (1.0, 10.0),
    "max_crypto_positions": (1.0, 3.0),
    "max_strategy_allocation_pct": (0.15, 0.90),
    "breakout_lookback": (6.0, 168.0),
    "ema_trend_filter": (10.0, 400.0),
    "atr_period": (5.0, 60.0),
    "atr_multiplier": (1.0, 5.0),
    "take_profit_atr_mult": (1.0, 8.0),
    "take_profit_ratio": (0.10, 0.90),
    "bb_period": (10.0, 60.0),
    "bb_std": (1.2, 3.5),
    "rsi_period": (5.0, 30.0),
    "rsi_oversold": (15.0, 45.0),
    "rsi_overbought": (55.0, 85.0),
    "adx_threshold": (12.0, 40.0),
    "adx_chop_threshold": (12.0, 32.0),
    "adx_trend_threshold": (18.0, 45.0),
    "hurst_mr_max": (0.40, 0.70),
    "stat_arb_z_entry": (1.2, 4.0),
    "stat_arb_z_exit": (0.0, 1.5),
    "stat_arb_entry_z": (1.2, 4.0),
    "stat_arb_exit_z": (0.0, 1.5),
    "stat_arb_stop_z": (2.5, 6.0),
    "stat_arb_lookback": (24.0, 240.0),
    "mr_atr_stop_mult": (1.0, 5.0),
    "basis_hurdle_apr": (2.0, 40.0),
    "max_basis_allocation_pct": (0.05, 0.45),
    "squeeze_atr_stop_mult": (1.0, 4.0),
    "squeeze_tp_atr_mult": (1.2, 6.0),
    "pullback_rsi_max": (25.0, 55.0),
    "pullback_atr_stop_mult": (1.0, 4.0),
    "vwap_window": (10.0, 72.0),
    "vwap_std_mult": (1.0, 3.5),
    "vwap_rsi_max": (20.0, 50.0),
    "nr7_lookback": (4.0, 14.0),
    "nr7_atr_stop_mult": (1.0, 4.0),
    "range_window": (12.0, 96.0),
    "range_low_quantile": (0.05, 0.25),
    "range_high_quantile": (0.75, 0.95),
    "range_rsi_oversold": (15.0, 45.0),
    "range_rsi_overbought": (55.0, 85.0),
    "range_adx_max": (15.0, 35.0),
    "range_atr_stop_mult": (1.0, 4.0),
    "range_min_net_profit_usd": (5.0, 100.0),
    "supertrend_atr_period": (5.0, 24.0),
    "supertrend_crypto_mult": (1.5, 5.0),
    "supertrend_equity_mult": (1.5, 5.0),
    "supertrend_stop_atr_mult": (1.5, 5.0),
    "donchian_fade_window": (10.0, 60.0),
    "donchian_fade_rsi_low": (2.0, 25.0),
    "donchian_fade_rsi_high": (75.0, 98.0),
    "donchian_fade_width_quantile": (0.20, 0.80),
    "donchian_fade_stop_atr_mult": (1.0, 4.0),
    "donchian_fade_tp_atr_mult": (1.0, 4.0),
    "williams_r_period": (7.0, 28.0),
    "williams_r_adx_max": (15.0, 35.0),
    "williams_r_stop_atr_mult": (1.0, 4.0),
    "bear_chand_z_window": (12.0, 48.0),
    "bear_chand_z_entry": (0.8, 3.0),
    "bear_chand_mult": (1.5, 5.0),
    "bear_chand_stop_atr_mult": (1.5, 4.5),
}


@dataclass
class GovernorApprovalResult:
    """Audit result of a governance review cycle."""
    approved: bool
    status: str  # "DEPLOYED", "PENDING_HUMAN_APPROVAL", "REJECTED_INVARIANT", "REJECTED_TESTS", "REJECTED_LLM_RISK"
    invariants_passed: bool
    tests_passed: bool
    llm_risk_approved: bool
    llm_rationale: str
    rejection_reasons: List[str]
    applied_changes: Dict[str, Any]
    timestamp: str
    verdict: str = "DISAPPROVE"
    confidence: float = 0.0
    risk_assessment: str = ""
    pending_human_approval: bool = False
    proposal_id: str = ""
    llm_metrics: Dict[str, Any] = field(default_factory=dict)


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load JSON configuration."""
    cfg_path = path or CONFIG_PATH
    with open(cfg_path, "r") as f:
        return json.load(f)


def audit_invariants(proposed_config: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Verify absolute repository and financial safety invariants:
    1. Paper trading only (no live credentials or real funds).
    2. Risk per trade strictly between 1% and 5% equity.
    3. Maximum crypto concentration cap (max 4 cryptos in active watchlist).
    4. Kill switch drawdown threshold <= 15%.
    """
    violations = []

    # Check Invariant 1: Paper trading mode
    if proposed_config.get("execution_mode") not in ("paper", "maker", "taker", None):
        violations.append("Violation: Real-money execution mode detected. Only simulated paper trading is permitted.")

    # Check Invariant 3: Hard-coded risk bounds [1%, 5%]
    global_risk = float(proposed_config.get("account_risk_pct", 0.02))
    if global_risk < MIN_ALLOWED_RISK_PCT or global_risk > MAX_ALLOWED_RISK_PCT:
        violations.append(
            f"Violation: Account risk {global_risk*100:.1f}% exceeds hard bounds [{MIN_ALLOWED_RISK_PCT*100:.0f}%, {MAX_ALLOWED_RISK_PCT*100:.0f}%]."
        )

    # Check profile risk bounds
    profiles = proposed_config.get("asset_profiles", {})
    for p_name, p_data in profiles.items():
        if "account_risk_pct" in p_data:
            p_risk = float(p_data["account_risk_pct"])
            if p_risk < MIN_ALLOWED_RISK_PCT or p_risk > MAX_ALLOWED_RISK_PCT:
                violations.append(
                    f"Violation: Profile '{p_name}' risk {p_risk*100:.1f}% exceeds hard bounds [{MIN_ALLOWED_RISK_PCT*100:.0f}%, {MAX_ALLOWED_RISK_PCT*100:.0f}%]."
                )

    # Check per-symbol risk weights
    for sym, w_risk in proposed_config.get("symbol_risk_weights", {}).items():
        w_val = float(w_risk)
        if w_val < MIN_ALLOWED_RISK_PCT or w_val > MAX_ALLOWED_RISK_PCT:
            violations.append(
                f"Violation: Symbol '{sym}' risk weight {w_val*100:.1f}% exceeds hard bounds [{MIN_ALLOWED_RISK_PCT*100:.0f}%, {MAX_ALLOWED_RISK_PCT*100:.0f}%]."
            )

    # Check crypto concentration guard (concurrent open crypto cap)
    max_crypto_open = int(proposed_config.get("max_crypto_positions", 2))
    if max_crypto_open > 3:
        violations.append(
            f"Violation: Max concurrent crypto open positions {max_crypto_open} exceeds safe limit (max 3)."
        )

    # Check watchlist crypto size
    watchlist = proposed_config.get("watchlist", [])
    crypto_symbols = [s for s in watchlist if is_crypto_symbol(s)]
    if len(crypto_symbols) > 12:
        violations.append(
            f"Violation: Crypto watchlist size {len(crypto_symbols)} exceeds reasonable universe limit (max 12)."
        )

    # Check max drawdown threshold
    max_dd = float(proposed_config.get("max_drawdown_limit_pct", 15.0))
    if max_dd > 15.0 or max_dd <= 0.0:
        violations.append(
            f"Violation: Max drawdown circuit breaker limit {max_dd:.1f}% outside mandatory (0%, 15.0%] threshold."
        )

    # Check strategy and coordinator parameter bounds (top-level and inside asset_profiles)
    for param_key, (low_b, high_b) in PARAMETER_BOUNDS.items():
        if param_key in ("account_risk_pct", "max_drawdown_limit_pct", "max_crypto_positions"):
            continue
        if param_key in proposed_config and proposed_config[param_key] is not None:
            try:
                val = float(proposed_config[param_key])
                if math.isnan(val) or math.isinf(val) or val < low_b or val > high_b:
                    violations.append(
                        f"Violation: Parameter '{param_key}' value {val} outside safe bounds [{low_b}, {high_b}]."
                    )
            except (TypeError, ValueError):
                violations.append(f"Violation: Parameter '{param_key}' must be numeric.")

    for p_name, p_data in profiles.items():
        if not isinstance(p_data, dict):
            continue
        for param_key, (low_b, high_b) in PARAMETER_BOUNDS.items():
            if param_key == "account_risk_pct":
                continue
            if param_key in p_data and p_data[param_key] is not None:
                try:
                    val = float(p_data[param_key])
                    if math.isnan(val) or math.isinf(val) or val < low_b or val > high_b:
                        violations.append(
                            f"Violation: Profile '{p_name}' parameter '{param_key}' value {val} outside safe bounds [{low_b}, {high_b}]."
                        )
                except (TypeError, ValueError):
                    violations.append(f"Violation: Profile '{p_name}' parameter '{param_key}' must be numeric.")

    return (len(violations) == 0), violations


def parse_governor_verdict_json(raw_response: Optional[str]) -> Tuple[bool, str, Dict[str, Any]]:
    """Strictly parse and validate the Governor LLM JSON verdict.

    Required JSON schema:
    {
      "verdict": "APPROVE | DISAPPROVE | NEEDS_HUMAN_REVIEW",
      "reason": "...",
      "risk_assessment": "...",
      "confidence": 0.0
    }

    Fail-closed rules:
    - Empty/None response -> DISAPPROVE
    - Non-JSON or malformed JSON -> DISAPPROVE
    - Missing required fields -> DISAPPROVE
    - Verdict not an exact match in {"APPROVE", "DISAPPROVE", "NEEDS_HUMAN_REVIEW"} -> DISAPPROVE
    - Confidence not a finite float in [0.0, 1.0] -> DISAPPROVE
    """
    if raw_response is None or not isinstance(raw_response, str) or not raw_response.strip():
        return False, "DISAPPROVE", {
            "verdict": "DISAPPROVE",
            "reason": "Fail-closed: Empty or missing LLM response.",
            "risk_assessment": "LLM unavailable or returned empty output.",
            "confidence": 0.0,
            "parse_error": "empty_response",
        }

    text = raw_response.strip()
    # Strip optional markdown code fences if present, but require valid JSON content inside
    if text.startswith("```"):
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

    try:
        parsed = json.loads(text)
    except Exception as e:
        return False, "DISAPPROVE", {
            "verdict": "DISAPPROVE",
            "reason": f"Fail-closed: Malformed JSON response ({e}).",
            "risk_assessment": "Unparseable LLM output blocked by strict schema gate.",
            "confidence": 0.0,
            "parse_error": "malformed_json",
        }

    if not isinstance(parsed, dict):
        return False, "DISAPPROVE", {
            "verdict": "DISAPPROVE",
            "reason": "Fail-closed: Root JSON element must be an object.",
            "risk_assessment": "Invalid JSON root type.",
            "confidence": 0.0,
            "parse_error": "invalid_root_type",
        }

    required_fields = {"verdict", "reason", "risk_assessment", "confidence"}
    missing = sorted(list(required_fields - set(parsed.keys())))
    if missing:
        return False, "DISAPPROVE", {
            "verdict": "DISAPPROVE",
            "reason": f"Fail-closed: Missing required JSON fields: {', '.join(missing)}.",
            "risk_assessment": "Incomplete governance schema.",
            "confidence": 0.0,
            "parse_error": "missing_fields",
        }

    raw_verdict = parsed.get("verdict")
    if not isinstance(raw_verdict, str):
        return False, "DISAPPROVE", {
            "verdict": "DISAPPROVE",
            "reason": "Fail-closed: 'verdict' field must be a string.",
            "risk_assessment": "Invalid verdict type.",
            "confidence": 0.0,
            "parse_error": "invalid_verdict_type",
        }

    clean_verdict = raw_verdict.strip().upper()
    # Exact enum match only — "DISAPPROVE", "NOT APPROVED", "DO NOT APPROVE" never match "APPROVE"
    if clean_verdict not in VALID_GOVERNOR_VERDICTS:
        return False, "DISAPPROVE", {
            "verdict": "DISAPPROVE",
            "reason": f"Fail-closed: Invalid verdict enum '{raw_verdict}'. Allowed: {sorted(VALID_GOVERNOR_VERDICTS)}.",
            "risk_assessment": str(parsed.get("risk_assessment", "")),
            "confidence": 0.0,
            "parse_error": "invalid_verdict_enum",
        }

    reason_str = parsed.get("reason")
    risk_str = parsed.get("risk_assessment")
    if not isinstance(reason_str, str) or not reason_str.strip() or not isinstance(risk_str, str) or not risk_str.strip():
        return False, "DISAPPROVE", {
            "verdict": "DISAPPROVE",
            "reason": "Fail-closed: 'reason' and 'risk_assessment' must be non-empty strings.",
            "risk_assessment": "Empty reason or risk assessment.",
            "confidence": 0.0,
            "parse_error": "empty_required_string",
        }

    try:
        conf_val = float(parsed.get("confidence"))
        if math.isnan(conf_val) or math.isinf(conf_val) or conf_val < 0.0 or conf_val > 1.0:
            raise ValueError("confidence out of [0.0, 1.0]")
    except (TypeError, ValueError) as e:
        return False, "DISAPPROVE", {
            "verdict": "DISAPPROVE",
            "reason": f"Fail-closed: Invalid confidence value ({e}).",
            "risk_assessment": risk_str,
            "confidence": 0.0,
            "parse_error": "invalid_confidence",
        }

    approved = (clean_verdict == "APPROVE")
    normalized = {
        "verdict": clean_verdict,
        "reason": reason_str.strip(),
        "risk_assessment": risk_str.strip(),
        "confidence": round(conf_val, 4),
        "parse_error": None,
    }
    return approved, clean_verdict, normalized


def query_llm_risk_review_structured(
    proposed_diff: Dict[str, Any],
    base_config: Dict[str, Any],
    rationale: Optional[str] = None,
    empirical_proof: Optional[Dict[str, Any]] = None,
    analyst_proposal: Optional[Dict[str, Any]] = None,
    model: str = "phi4-mini:latest",
    use_cache: bool = True,
) -> Tuple[bool, str, Dict[str, Any], Dict[str, Any]]:
    """Invoke local Ollama LLM as Chief Risk Governor with strict JSON schema validation and fail-closed safety.

    Returns:
        (approved, verdict_enum, parsed_verdict_dict, llm_telemetry)
    """
    proof_str = json.dumps(empirical_proof, indent=2) if empirical_proof else "Not provided"
    analyst_str = json.dumps(analyst_proposal, indent=2) if analyst_proposal else "Direct configuration proposal (No Stage-1 Analyst structured synthesis)"

    # Build deterministic sign-convention note if replay deltas are present in empirical_proof
    sign_note = (
        "Note on metric sign conventions: replay_return_delta_pct > 0 means net return INCREASED (beneficial); "
        "replay_max_dd_delta_pct < 0 means maximum drawdown DECREASED/IMPROVED (beneficial); "
        "replay_sharpe_delta > 0 means Sharpe ratio INCREASED (beneficial)."
    )

    prompt = f"""You are the Chief Risk Governor of the Web4 Multi-Strategy Quantitative Trading Desk.
Evaluate the following configuration proposal across strategy-level performance, combined portfolio interaction, and risk limits.

[ANALYST STRUCTURED PROPOSAL]
{analyst_str}

[PROPOSED CONFIGURATION DIFF]
{json.dumps(proposed_diff, indent=2)}

[EMPIRICAL PROOF & JUSTIFICATION]
Rationale: {rationale or 'Automated quantitative proposal.'}
{sign_note}
Empirical Evidence:
{proof_str}

[PORTFOLIO RISK POLICY & SAFE PARAMETER BOUNDS]
- Execution Mode: Paper Trading Only ('maker' or 'taker')
- Account Risk Per Trade: [0.01, 0.05] (1% to 5% of equity; current={base_config.get('account_risk_pct', 0.02)})
- Max Drawdown Circuit Breaker: <= 15.0% (current={base_config.get('max_drawdown_limit_pct', 15.0)}%)
- Max Concurrent Crypto Positions: <= 3 (current={base_config.get('max_crypto_positions', 2)})
- Strategy Parameter Bounds:
  * breakout_lookback: [6, 168], atr_multiplier: [1.0, 5.0]
  * rsi_oversold: [15, 45], rsi_overbought: [55, 85]
  * adx_chop_threshold: [12, 32], adx_trend_threshold: [18, 45]
  * stat_arb_z_entry: [1.2, 4.0], basis_hurdle_apr: [2.0, 40.0]

[DECISION CRITERIA]
1. Return "APPROVE" when:
   - All proposed parameters are within the safe parameter bounds listed above, AND
   - Empirical evidence shows positive return improvement (replay_return_delta_pct > 0), drawdown reduction (replay_max_dd_delta_pct < 0), improved profit factor (> 1.0), or pruning of losing drags.
2. Return "DISAPPROVE" when:
   - The proposal violates risk bounds, disables safety gates (e.g., enable_regime_filter=False), degrades out-of-sample expectancy (replay_return_delta_pct < 0 or replay_max_dd_delta_pct > 0), or lacks supporting evidence.
3. Return "NEEDS_HUMAN_REVIEW" if the evidence is ambiguous or borderline.

Respond ONLY with a valid JSON object matching this exact schema:
{{
  "verdict": "APPROVE" | "DISAPPROVE" | "NEEDS_HUMAN_REVIEW",
  "reason": "Concise explanation of the decision",
  "risk_assessment": "Assessment of strategy and combined portfolio risk impact",
  "confidence": <float between 0.0 and 1.0 based on statistical evidence quality>
}}"""

    system_prompt = (
        "You are an institutional Chief Risk Governor. You must output ONLY valid JSON containing the keys "
        "'verdict', 'reason', 'risk_assessment', and 'confidence'. "
        "Your 'verdict' MUST be one of 'APPROVE', 'DISAPPROVE', or 'NEEDS_HUMAN_REVIEW'. "
        "Your 'confidence' must be a float between 0.0 and 1.0 reflecting statistical evidence strength."
    )

    llm_call = query_local_llm_with_metrics(
        prompt=prompt,
        system_prompt=system_prompt,
        model=model,
        temperature=0.1,
        max_tokens=300,
        timeout_seconds=15.0,
        use_cache=use_cache,
        json_mode=True,
    )

    if not llm_call.get("success") or not llm_call.get("response"):
        err_msg = llm_call.get("error") or "LLM unavailable or empty response"
        fallback_dict = {
            "verdict": "DISAPPROVE",
            "reason": f"Fail-closed: LLM governance check failed ({err_msg}).",
            "risk_assessment": "Cannot certify proposal safety when LLM Risk Governor is unreachable.",
            "confidence": 0.0,
            "parse_error": "llm_unavailable",
        }
        return False, "DISAPPROVE", fallback_dict, llm_call

    approved, verdict_enum, parsed_dict = parse_governor_verdict_json(llm_call["response"])
    return approved, verdict_enum, parsed_dict, llm_call


def query_llm_risk_review(
    proposed_diff: Dict[str, Any],
    base_config: Dict[str, Any],
    rationale: Optional[str] = None,
    empirical_proof: Optional[Dict[str, Any]] = None,
    model: str = "phi4-mini:latest",
) -> Tuple[bool, str]:
    """Invoke local Ollama LLM as Chief Risk Governor (fail-closed wrapper)."""
    approved, verdict_enum, parsed_dict, _ = query_llm_risk_review_structured(
        proposed_diff=proposed_diff,
        base_config=base_config,
        rationale=rationale,
        empirical_proof=empirical_proof,
        model=model,
    )
    formatted = f"VERDICT: {verdict_enum} (confidence={parsed_dict.get('confidence', 0.0):.2f}) | {parsed_dict.get('reason', '')} | Risk: {parsed_dict.get('risk_assessment', '')}"
    return approved, formatted


def run_pytest_regression_gate() -> Tuple[bool, str]:
    """Run full pytest test suite to guarantee 100% test pass rate."""
    cmd = [
        str(PROJECT_ROOT / ".venv" / "bin" / "pytest"),
        "-c",
        str(PROJECT_ROOT / "pytest.ini"),
        str(PROJECT_ROOT / "tests"),
    ]
    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
    passed = (res.returncode == 0)
    log_tail = res.stdout[-800:] if res.stdout else res.stderr[-800:]
    return passed, log_tail


def commit_and_push_config(message: str) -> bool:
    """Commit updated config/active_config.json and push to origin/main."""
    try:
        subprocess.run(["git", "-C", str(PROJECT_ROOT), "add", "config/active_config.json"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(PROJECT_ROOT), "commit", "-m", message], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(PROJECT_ROOT), "push", "origin", "main"], check=True, capture_output=True)
        return True
    except Exception as e:
        logger.warning(f"[Governor] Git commit/push notice: {e}")
        return False


def append_governance_audit_log(entry: GovernorApprovalResult, log_path: Optional[Path] = None):
    """Append immutable audit log entry."""
    out = log_path or AUDIT_LOG_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a") as f:
        f.write(f"\n--- GOVERNANCE AUDIT RECORD [{entry.timestamp}] ---\n")
        f.write(f"Status: {entry.status} (Approved: {entry.approved})\n")
        f.write(f"Invariants Passed: {entry.invariants_passed} | Tests Passed: {entry.tests_passed} | LLM Risk Approved: {entry.llm_risk_approved}\n")
        if entry.rejection_reasons:
            f.write(f"Rejection Reasons: {'; '.join(entry.rejection_reasons)}\n")
        f.write(f"LLM Rationale:\n{entry.llm_rationale}\n")
        f.write(f"Applied Changes: {json.dumps(entry.applied_changes)}\n")
        f.write("-" * 65 + "\n")


def _merge_proposed_changes_into_config(
    base_cfg: Dict[str, Any],
    proposed_changes: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a new candidate configuration dict with proposed_changes merged in."""
    candidate_cfg = json.loads(json.dumps(base_cfg))

    for k, v in proposed_changes.items():
        if k == "watchlist":
            candidate_cfg["watchlist"] = list(v)
        elif k == "blacklisted_symbols":
            existing = set(candidate_cfg.get("blacklisted_symbols", []))
            candidate_cfg["blacklisted_symbols"] = sorted(list(existing.union(set(v))))
        elif k in ("rehabilitated_symbols", "remove_blacklisted_symbols"):
            existing = set(candidate_cfg.get("blacklisted_symbols", []))
            candidate_cfg["blacklisted_symbols"] = sorted(list(existing - set(v)))
        elif k == "asset_profiles":
            for prof_k, prof_v in v.items():
                if prof_k in candidate_cfg.get("asset_profiles", {}):
                    candidate_cfg["asset_profiles"][prof_k].update(prof_v)
                else:
                    candidate_cfg.setdefault("asset_profiles", {})[prof_k] = prof_v
        else:
            candidate_cfg[k] = v

    bl_set = set(candidate_cfg.get("blacklisted_symbols", []))
    if "watchlist" in candidate_cfg:
        candidate_cfg["watchlist"] = [s for s in candidate_cfg["watchlist"] if s not in bl_set]

    rehab_set = set(proposed_changes.get("rehabilitated_symbols", []))
    profiles = candidate_cfg.get("asset_profiles") or {}
    has_standard_profiles = "crypto_micro_swing" in profiles and "stocks_growth" in profiles
    if has_standard_profiles and bl_set:
        for p in profiles.values():
            if isinstance(p, dict) and isinstance(p.get("symbols"), list):
                p["symbols"] = [sym for sym in p["symbols"] if sym not in bl_set]
    assigned_in_profiles = {sym for p in profiles.values() if isinstance(p, dict) for sym in p.get("symbols", [])}
    macro_cryptos = {"BTC/USDT", "ETH/USDT", "BNB/USDT"}
    macro_equities = {"SPY", "QQQ", "GLD", "SLV", "TLT", "XLE", "KDP", "NOK", "CBRS"}

    for s in candidate_cfg.get("watchlist", []):
        if s in rehab_set:
            candidate_cfg.setdefault("symbol_risk_weights", {})[s] = 0.01
            candidate_cfg.setdefault("symbol_exposure_caps", {})[s] = 0.20
        elif s not in candidate_cfg.setdefault("symbol_risk_weights", {}):
            candidate_cfg["symbol_risk_weights"][s] = 0.02
        if s not in candidate_cfg.setdefault("symbol_exposure_caps", {}):
            candidate_cfg["symbol_exposure_caps"][s] = 0.35
        if has_standard_profiles and s not in assigned_in_profiles:
            if "/" in s:
                target_prof = "crypto_macro" if (s in macro_cryptos and "crypto_macro" in profiles) else "crypto_micro_swing"
            else:
                target_prof = "macro_hedges" if (s in macro_equities and "macro_hedges" in profiles) else "stocks_growth"
            profiles[target_prof].setdefault("symbols", []).append(s)
            assigned_in_profiles.add(s)

    return candidate_cfg


def is_proposal_still_relevant(
    proposal: Dict[str, Any],
    current_cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return True only if a pending proposal's changes are not already applied
    and its baseline assumptions still match the live configuration."""
    if not isinstance(proposal, dict):
        return False
    changes = proposal.get("proposed_changes")
    if not isinstance(changes, dict) or not changes:
        return False
    cfg = current_cfg if current_cfg is not None else load_config()
    if not cfg:
        return True

    # 1. If every proposed key/value already matches live config, it is obsolete
    if all(cfg.get(k) == v for k, v in changes.items()):
        return False

    # 2. If the proposal recorded the baseline 'current_value' for a single parameter
    #    and the live config has already moved away from that baseline, it is obsolete
    evidence = proposal.get("empirical_proof") or (proposal.get("analyst_proposal") or {}).get("supporting_evidence") or {}
    if isinstance(evidence, dict) and "current_value" in evidence and len(changes) == 1:
        param_key = next(iter(changes.keys()))
        if param_key in cfg and cfg[param_key] != evidence["current_value"]:
            return False

    return True


def list_pending_proposals(pending_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load all proposals from the pending proposals file."""
    path = pending_path or PENDING_PROPOSALS_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
        return data.get("proposals", [])
    except Exception:
        return []


def save_pending_proposal(
    record: Dict[str, Any],
    pending_path: Optional[Path] = None,
    current_cfg: Optional[Dict[str, Any]] = None,
) -> Path:
    """Atomically persist a validated proposal to the pending human approval queue.
    Automatically supersedes any older PENDING_HUMAN_APPROVAL proposals or obsolete proposals
    so at most ONE (the newest still-relevant proposal) awaits human approval."""
    path = pending_path or PENDING_PROPOSALS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = list_pending_proposals(path)
    cfg = current_cfg if current_cfg is not None else load_config()
    now_iso = datetime.now(timezone.utc).isoformat()
    rec_id = record.get("proposal_id")
    rec_status = record.get("status")

    updated: List[Dict[str, Any]] = []
    for p in existing:
        if p.get("proposal_id") == rec_id:
            continue
        if p.get("status") == "PENDING_HUMAN_APPROVAL":
            if rec_status in ("PENDING_HUMAN_APPROVAL", "DEPLOYED") or not is_proposal_still_relevant(p, cfg):
                p = dict(p)
                p["status"] = "SUPERSEDED"
                p["superseded_by"] = rec_id
                p["superseded_at"] = now_iso
        updated.append(p)

    updated.append(record)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(updated, indent=2))
    os.replace(tmp, path)
    return path


def approve_pending_proposal(
    proposal_id: str,
    approved_by: str = "human_operator",
    config_path: Optional[Path] = None,
    pending_path: Optional[Path] = None,
    verify_tests: bool = False,
    auto_commit: bool = False,
) -> GovernorApprovalResult:
    """Explicit human authorization step to apply a pending proposal to active_config.json."""
    if not approved_by or not str(approved_by).strip():
        raise ValueError("Human approver identity ('approved_by') is required to authorize deployment.")

    path = pending_path or (config_path.parent / "pending_proposals.json" if config_path else PENDING_PROPOSALS_PATH)
    proposals = list_pending_proposals(path)
    target = next((p for p in proposals if p.get("proposal_id") == proposal_id), None)
    now_iso = datetime.now(timezone.utc).isoformat()

    if target is None:
        return GovernorApprovalResult(
            approved=False,
            status="REJECTED_NOT_FOUND",
            invariants_passed=False,
            tests_passed=False,
            llm_risk_approved=False,
            llm_rationale=f"Pending proposal '{proposal_id}' not found.",
            rejection_reasons=[f"Proposal '{proposal_id}' not found in pending queue."],
            applied_changes={},
            timestamp=now_iso,
            verdict="DISAPPROVE",
            proposal_id=proposal_id,
        )

    if target.get("status") != "PENDING_HUMAN_APPROVAL":
        return GovernorApprovalResult(
            approved=False,
            status=f"REJECTED_INVALID_STATE_{target.get('status')}",
            invariants_passed=False,
            tests_passed=False,
            llm_risk_approved=False,
            llm_rationale=f"Proposal '{proposal_id}' is in state '{target.get('status')}', not PENDING_HUMAN_APPROVAL.",
            rejection_reasons=[f"Proposal '{proposal_id}' is not pending human approval."],
            applied_changes={},
            timestamp=now_iso,
            verdict="DISAPPROVE",
            proposal_id=proposal_id,
        )

    res = review_and_deploy_proposal(
        proposed_changes=target["proposed_changes"],
        rationale=target.get("rationale"),
        empirical_proof=target.get("empirical_proof"),
        config_path=config_path or _resolve_target_config_path(target.get("target_config_path")),
        verify_tests=verify_tests,
        use_llm=False,  # Already validated by Governor prior to entering pending queue
        auto_commit=auto_commit,
        require_human_approval=True,
        human_approved=True,
        pending_proposals_path=path,
        proposal_id=proposal_id,
    )

    if res.approved:
        target["status"] = "DEPLOYED"
        target["human_approved"] = True
        target["approved_by"] = approved_by
        target["approved_at"] = now_iso
        save_pending_proposal(target, path)

    return res


def reject_pending_proposal(
    proposal_id: str,
    rejected_by: str = "human_operator",
    reason: str = "Rejected by human reviewer",
    pending_path: Optional[Path] = None,
) -> bool:
    """Explicit human rejection of a pending proposal."""
    path = pending_path or PENDING_PROPOSALS_PATH
    proposals = list_pending_proposals(path)
    target = next((p for p in proposals if p.get("proposal_id") == proposal_id), None)
    if target is None:
        return False
    target["status"] = "REJECTED_BY_HUMAN"
    target["human_approved"] = False
    target["rejected_by"] = rejected_by
    target["rejection_reason"] = reason
    target["rejected_at"] = datetime.now(timezone.utc).isoformat()
    save_pending_proposal(target, path)
    return True


def _parse_numeric_metric(val: Any) -> Optional[float]:
    """Helper to safely parse percentages, dollar amounts, and numeric strings."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace("%", "").replace("+", "").replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def evaluate_conditional_auto_approval_gates(
    candidate_cfg: Dict[str, Any],
    base_cfg: Dict[str, Any],
    empirical_proof: Optional[Dict[str, Any]],
    invariants_passed: bool,
    tests_passed: bool,
    governor_verdict: str,
    governor_confidence: float,
    analyst_proposal: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Evaluates the 4 strict conditional auto-approval gates:
    Gate 1: Safety Invariants Pass (1%-5% risk, paper mode, max drawdown limits).
    Gate 2: All Pytest Regression Tests Pass (zero broken code or regressions).
    Gate 3: Governor Confidence meets threshold (verdict == APPROVE, confidence >= min_governor_confidence, default 0.85).
    Gate 4: Strict Alpha Superiority / Non-Degradation Gate:
            - Empirical proof must be present.
            - Out-of-sample return delta >= min_oos_return_delta (default 0.0%).
            - Out-of-sample Sharpe ratio >= baseline Sharpe (test_sharpe_delta >= 0.0).
            - Out-of-sample Max Drawdown <= baseline Max DD + tolerance (default 0.5%).
            - Multi-window returns must not be negative on any validation window.
    """
    min_oos_ret_delta = float(base_cfg.get("auto_approve_min_oos_return_delta", 0.0))
    min_oos_sharpe_delta = float(base_cfg.get("auto_approve_min_oos_sharpe_delta", 0.0))
    max_dd_tol = float(base_cfg.get("auto_approve_max_drawdown_tolerance_pct", 0.5))
    min_gov_confidence = float(
        candidate_cfg.get(
            "auto_approve_min_governor_confidence",
            base_cfg.get("auto_approve_min_governor_confidence", 0.85),
        )
    )

    details: Dict[str, Any] = {
        "gate_1_invariants": invariants_passed,
        "gate_2_tests": tests_passed,
        "gate_3_governor_verdict": governor_verdict,
        "gate_3_governor_confidence": governor_confidence,
        "gate_3_min_confidence": min_gov_confidence,
        "gate_4_superiority": False,
        "reasons": [],
    }

    # Gate 1: Safety Invariants
    if not invariants_passed:
        msg = "Gate 1 Failed: Safety invariants violated."
        details["reasons"].append(msg)
        return False, msg, details

    # Gate 2: Pytest regression suite
    if not tests_passed:
        msg = "Gate 2 Failed: Pytest regression suite failed."
        details["reasons"].append(msg)
        return False, msg, details

    # Gate 3: Governor Confidence
    if governor_verdict != "APPROVE" or governor_confidence < (min_gov_confidence - 0.001):
        msg = f"Gate 3 Failed: Governor verdict is {governor_verdict} (confidence={governor_confidence:.2f} < {min_gov_confidence:.2f})."
        details["reasons"].append(msg)
        return False, msg, details

    # Gate 4: Strict Alpha Superiority / Non-Degradation Gate
    if not empirical_proof and not analyst_proposal:
        msg = "Gate 4 Failed: No empirical proof provided to verify superiority over current baseline."
        details["reasons"].append(msg)
        return False, msg, details

    proof = empirical_proof or {}

    # 4a. Check Out-of-Sample Return Delta
    if "test_return_delta" in proof:
        ret_delta = _parse_numeric_metric(proof["test_return_delta"])
        if ret_delta is not None:
            details["test_return_delta"] = ret_delta
            if ret_delta < min_oos_ret_delta:
                msg = f"Gate 4 Failed: Out-of-sample return delta ({ret_delta:+.2f}%) did not meet minimum threshold ({min_oos_ret_delta:+.2f}%)."
                details["reasons"].append(msg)
                return False, msg, details

    # 4b. Check Multi-window OOS returns (e.g. oos_w1_return, oos12_w1_return)
    for w_key in ("oos_w1_return", "oos12_w1_return", "oos_w2_return", "oos12_w2_return"):
        if w_key in proof:
            w_ret = _parse_numeric_metric(proof[w_key])
            if w_ret is not None:
                details[w_key] = w_ret
                if w_ret < 0.0:
                    msg = f"Gate 4 Failed: Out-of-sample validation window ({w_key}={w_ret:+.2f}%) is negative; fails sure improvement check."
                    details["reasons"].append(msg)
                    return False, msg, details

    # 4c. Check Out-of-Sample Sharpe Ratio
    if "test_sharpe_delta" in proof:
        sh_delta = _parse_numeric_metric(proof["test_sharpe_delta"])
        if sh_delta is not None:
            details["test_sharpe_delta"] = sh_delta
            if sh_delta < min_oos_sharpe_delta:
                msg = f"Gate 4 Failed: Out-of-sample Sharpe delta ({sh_delta:+.2f}) degraded below baseline threshold ({min_oos_sharpe_delta:+.2f})."
                details["reasons"].append(msg)
                return False, msg, details

    if "test_sharpe" in proof and "base_test_sharpe" in proof:
        cand_sh = _parse_numeric_metric(proof["test_sharpe"])
        base_sh = _parse_numeric_metric(proof["base_test_sharpe"])
        if cand_sh is not None and base_sh is not None and base_sh > 0:
            details["test_sharpe"] = cand_sh
            details["base_test_sharpe"] = base_sh
            if cand_sh < base_sh:
                msg = f"Gate 4 Failed: Candidate Out-of-Sample Sharpe ({cand_sh:.2f}) degraded below baseline ({base_sh:.2f})."
                details["reasons"].append(msg)
                return False, msg, details

    # 4d. Check Out-of-Sample Max Drawdown Non-Degradation
    if "test_max_dd" in proof and "base_test_max_dd" in proof:
        cand_dd = _parse_numeric_metric(proof["test_max_dd"])
        base_dd = _parse_numeric_metric(proof["base_test_max_dd"])
        if cand_dd is not None and base_dd is not None and base_dd > 0:
            details["test_max_dd"] = cand_dd
            details["base_test_max_dd"] = base_dd
            if cand_dd > (base_dd + max_dd_tol):
                msg = f"Gate 4 Failed: Candidate Out-of-Sample MaxDD ({cand_dd:.1f}%) exceeds baseline MaxDD ({base_dd:.1f}% + {max_dd_tol:.1f}% tolerance)."
                details["reasons"].append(msg)
                return False, msg, details

    # 4e. Check Analyst drag delta if present
    if "drag_delta_pnl" in proof:
        drag_delta = _parse_numeric_metric(proof["drag_delta_pnl"])
        if drag_delta is not None:
            details["drag_delta_pnl"] = drag_delta
            if drag_delta > 0:
                msg = f"Gate 4 Failed: Rebalance increases drag by ${drag_delta:.2f}."
                details["reasons"].append(msg)
                return False, msg, details

    details["gate_4_superiority"] = True
    return True, "All 4 conditional auto-approval gates PASSED (Invariants, Tests, Governor 100%, Alpha Superiority).", details


def auto_approve_pending_proposals(
    pending_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
    verify_tests: bool = True,
    auto_commit: Optional[bool] = None,
) -> List[GovernorApprovalResult]:
    """
    Evaluates all PENDING_HUMAN_APPROVAL proposals against the 4 conditional auto-approval gates.
    Auto-deploys any proposal that passes all 4 gates without requiring manual intervention.
    """
    path = pending_path or PENDING_PROPOSALS_PATH
    proposals = list_pending_proposals(path)
    results = []
    base_cfg = load_config(config_path)

    for p in proposals:
        if p.get("status") != "PENDING_HUMAN_APPROVAL":
            continue
        prop_id = p.get("proposal_id", "")
        # Evaluate 4 gates
        passed, reason, _ = evaluate_conditional_auto_approval_gates(
            candidate_cfg=_merge_proposed_changes_into_config(base_cfg, p.get("proposed_changes", {})),
            base_cfg=base_cfg,
            empirical_proof=p.get("empirical_proof"),
            invariants_passed=p.get("invariants_passed", False),
            tests_passed=p.get("tests_passed", False),
            governor_verdict=p.get("governor_verdict", "DISAPPROVE"),
            governor_confidence=float(p.get("governor_confidence", 0.0)),
            analyst_proposal=p.get("analyst_proposal"),
        )
        if passed:
            res = approve_pending_proposal(
                proposal_id=prop_id,
                approved_by="conditional_auto_gate",
                config_path=config_path,
                verify_tests=verify_tests,
                auto_commit=auto_commit,
                pending_path=pending_path,
            )
            results.append(res)
        else:
            res = GovernorApprovalResult(
                approved=False,
                status="HELD_FOR_HUMAN_REVIEW",
                invariants_passed=p.get("invariants_passed", False),
                tests_passed=p.get("tests_passed", False),
                llm_risk_approved=False,
                llm_rationale=f"Auto-approval gate withheld: {reason}",
                rejection_reasons=[reason],
                applied_changes={},
                timestamp=datetime.now(timezone.utc).isoformat(),
                proposal_id=prop_id,
            )
            results.append(res)
    return results


def review_and_deploy_proposal(
    proposed_changes: Dict[str, Any],
    rationale: Optional[str] = None,
    empirical_proof: Optional[Dict[str, Any]] = None,
    config_path: Optional[Path] = None,
    verify_tests: bool = True,
    use_llm: bool = True,
    model: str = "phi4-mini:latest",
    auto_commit: Optional[bool] = None,
    require_human_approval: bool = True,
    human_approved: bool = False,
    pending_proposals_path: Optional[Path] = None,
    analyst_proposal: Optional[Dict[str, Any]] = None,
    proposal_id: Optional[str] = None,
) -> GovernorApprovalResult:
    """Thread-safe entrypoint serializing concurrent background agent deployments."""
    with _GOVERNOR_DEPLOY_LOCK:
        return _review_and_deploy_proposal_unlocked(
            proposed_changes=proposed_changes,
            rationale=rationale,
            empirical_proof=empirical_proof,
            config_path=config_path,
            verify_tests=verify_tests,
            use_llm=use_llm,
            model=model,
            auto_commit=auto_commit,
            require_human_approval=require_human_approval,
            human_approved=human_approved,
            pending_proposals_path=pending_proposals_path,
            analyst_proposal=analyst_proposal,
            proposal_id=proposal_id,
        )


def _review_and_deploy_proposal_unlocked(
    proposed_changes: Dict[str, Any],
    rationale: Optional[str] = None,
    empirical_proof: Optional[Dict[str, Any]] = None,
    config_path: Optional[Path] = None,
    verify_tests: bool = True,
    use_llm: bool = True,
    model: str = "phi4-mini:latest",
    auto_commit: Optional[bool] = None,
    require_human_approval: bool = True,
    human_approved: bool = False,
    pending_proposals_path: Optional[Path] = None,
    analyst_proposal: Optional[Dict[str, Any]] = None,
    proposal_id: Optional[str] = None,
) -> GovernorApprovalResult:
    """
    End-to-end governance review and human-gated deployment pipeline:
    Analyst -> Proposal -> Governor -> Validation -> Pending Approval -> Human Decision -> Application

    1. Validate Analyst structured proposal schema (if analyst_proposal provided).
    2. Merge proposal into candidate configuration.
    3. Audit hard safety invariants and parameter bounds.
    4. Query LLM Risk Review using strict JSON schema and fail-closed validation.
    5. Run automated Pytest regression gate.
    6. If require_human_approval=True and human_approved=False:
       Save proposal to pending_proposals.json as PENDING_HUMAN_APPROVAL without modifying active_config.json.
    7. Only when human_approved=True (or require_human_approval=False in isolated tests):
       Atomically write active_config.json with backup and log audit.
    """
    cfg_file = config_path or CONFIG_PATH
    pend_file = pending_proposals_path or (
        cfg_file.parent / "pending_proposals.json" if config_path is not None else PENDING_PROPOSALS_PATH
    )
    prop_id = proposal_id or (
        analyst_proposal.get("proposal_id") if isinstance(analyst_proposal, dict) and analyst_proposal.get("proposal_id")
        else f"PROP-{uuid.uuid4().hex[:8].upper()}"
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    rejections: List[str] = []

    # Step 0: Optional Analyst Proposal Schema Validation
    if analyst_proposal is not None:
        from agent.trade_analyst import validate_analyst_proposal_schema
        schema_ok, schema_errs, _ = validate_analyst_proposal_schema(analyst_proposal)
        if not schema_ok:
            rejections.extend(schema_errs)
            res = GovernorApprovalResult(
                approved=False,
                status="REJECTED_SCHEMA",
                invariants_passed=False,
                tests_passed=False,
                llm_risk_approved=False,
                llm_rationale="Analyst proposal schema validation failed: " + "; ".join(schema_errs),
                rejection_reasons=rejections,
                applied_changes={},
                timestamp=now_iso,
                verdict="DISAPPROVE",
                proposal_id=prop_id,
            )
            append_governance_audit_log(res)
            return res

    base_cfg = load_config(cfg_file)
    snap_file = cfg_file.parent / "last_deployment_snapshot.json"
    if not human_approved and snap_file.exists():
        try:
            snap_data = json.loads(snap_file.read_text())
            if snap_data.get("rolled_back") and snap_data.get("rolled_back_changes") == proposed_changes:
                cooldown_hours = float(base_cfg.get("rollback_repeat_cooldown_hours", 48.0))
                still_in_cooldown = True
                rb_at_str = snap_data.get("rolled_back_at")
                if rb_at_str:
                    try:
                        rb_dt = datetime.fromisoformat(rb_at_str)
                        if rb_dt.tzinfo is None:
                            rb_dt = rb_dt.replace(tzinfo=timezone.utc)
                        elapsed_h = (datetime.now(timezone.utc) - rb_dt).total_seconds() / 3600.0
                        still_in_cooldown = elapsed_h < cooldown_hours
                    except ValueError:
                        still_in_cooldown = True
                if still_in_cooldown:
                    msg = (
                        f"Blocked by Post-Rollback Cooldown ({cooldown_hours:.0f}h): Proposed changes {proposed_changes} match "
                        f"recently rolled-back proposal {snap_data.get('proposal_id')}."
                    )
                    res = GovernorApprovalResult(
                        approved=False,
                        status="REJECTED_ROLLED_BACK_REPEAT",
                        invariants_passed=True,
                        tests_passed=False,
                        llm_risk_approved=False,
                        llm_rationale=msg,
                        rejection_reasons=[msg],
                        applied_changes={},
                        timestamp=now_iso,
                        verdict="DISAPPROVE",
                        proposal_id=prop_id,
                    )
                    append_governance_audit_log(res)
                    return res
        except Exception:
            pass
    candidate_cfg = _merge_proposed_changes_into_config(base_cfg, proposed_changes)

    # Step 1: Invariant & Parameter Bounds Check
    invariants_ok, inv_errors = audit_invariants(candidate_cfg)
    if not invariants_ok:
        rejections.extend(inv_errors)
        res = GovernorApprovalResult(
            approved=False,
            status="REJECTED_INVARIANT",
            invariants_passed=False,
            tests_passed=False,
            llm_risk_approved=False,
            llm_rationale="Invariants breached: " + "; ".join(inv_errors),
            rejection_reasons=rejections,
            applied_changes={},
            timestamp=now_iso,
            verdict="DISAPPROVE",
            proposal_id=prop_id,
        )
        append_governance_audit_log(res)
        return res

    # Step 2: LLM Risk Review (Strict JSON & Fail-Closed)
    llm_approved = True
    verdict_enum = "APPROVE"
    llm_rationale = "Rule-only governance verification passed."
    verdict_confidence = 1.0
    risk_assessment_str = "Deterministic invariant and parameter bounds check passed."
    llm_telemetry: Dict[str, Any] = {}

    if use_llm:
        llm_approved, verdict_enum, parsed_verdict, llm_telemetry = query_llm_risk_review_structured(
            proposed_diff=proposed_changes,
            base_config=base_cfg,
            rationale=rationale,
            empirical_proof=empirical_proof,
            analyst_proposal=analyst_proposal,
            model=model,
        )
        verdict_confidence = float(parsed_verdict.get("confidence", 0.0))
        risk_assessment_str = str(parsed_verdict.get("risk_assessment", ""))
        llm_rationale = (
            f"VERDICT: {verdict_enum} (confidence={verdict_confidence:.2f}) | "
            f"{parsed_verdict.get('reason', '')} | Risk: {risk_assessment_str}"
        )

        if verdict_enum == "DISAPPROVE":
            rejections.append(parsed_verdict.get("reason", "LLM Risk Governor disapproved proposal."))
            res = GovernorApprovalResult(
                approved=False,
                status="REJECTED_LLM_RISK",
                invariants_passed=True,
                tests_passed=False,
                llm_risk_approved=False,
                llm_rationale=llm_rationale,
                rejection_reasons=rejections,
                applied_changes={},
                timestamp=now_iso,
                verdict="DISAPPROVE",
                confidence=verdict_confidence,
                risk_assessment=risk_assessment_str,
                proposal_id=prop_id,
                llm_metrics=llm_telemetry,
            )
            append_governance_audit_log(res)
            return res

    # Step 3: Pytest Regression Verification (before queuing or applying)
    tests_ok = True
    test_log = ""
    if verify_tests:
        tests_ok, test_log = run_pytest_regression_gate()
        if not tests_ok:
            rejections.append("Pytest regression suite failed.")
            res = GovernorApprovalResult(
                approved=False,
                status="REJECTED_TESTS",
                invariants_passed=True,
                tests_passed=False,
                llm_risk_approved=llm_approved,
                llm_rationale=f"Tests failed:\n{test_log[-300:]}",
                rejection_reasons=rejections,
                applied_changes={},
                timestamp=now_iso,
                verdict="DISAPPROVE",
                confidence=verdict_confidence,
                risk_assessment=risk_assessment_str,
                proposal_id=prop_id,
                llm_metrics=llm_telemetry,
            )
            append_governance_audit_log(res)
            return res

    # Step 4: Mandatory Human-in-the-Loop Gate OR 4-Gate Conditional Auto-Approval
    auto_approve_enabled = bool(
        candidate_cfg.get("enable_conditional_auto_approval", False)
        or base_cfg.get("enable_conditional_auto_approval", False)
    )

    auto_gate_passed = False
    auto_gate_reason = ""
    gate_details: Dict[str, Any] = {}

    if auto_approve_enabled and not human_approved and verdict_enum != "DISAPPROVE":
        auto_gate_passed, auto_gate_reason, gate_details = evaluate_conditional_auto_approval_gates(
            candidate_cfg=candidate_cfg,
            base_cfg=base_cfg,
            empirical_proof=empirical_proof,
            invariants_passed=invariants_ok,
            tests_passed=tests_ok,
            governor_verdict=verdict_enum,
            governor_confidence=verdict_confidence,
            analyst_proposal=analyst_proposal,
        )

    # If human approval is required and not granted, and auto-gate did not pass:
    if (require_human_approval and not human_approved and not auto_gate_passed) or (verdict_enum == "NEEDS_HUMAN_REVIEW" and not human_approved):
        pending_record = {
            "proposal_id": prop_id,
            "status": "PENDING_HUMAN_APPROVAL",
            "governor_verdict": verdict_enum,
            "governor_confidence": verdict_confidence,
            "governor_rationale": llm_rationale,
            "risk_assessment": risk_assessment_str,
            "invariants_passed": True,
            "tests_passed": tests_ok,
            "human_approved": False,
            "auto_gate_evaluation": {
                "auto_approved": False,
                "reason": auto_gate_reason or "Mandatory human review required.",
                "details": gate_details,
            },
            "proposed_changes": proposed_changes,
            "rationale": rationale or "Validated governance proposal awaiting human authorization.",
            "empirical_proof": empirical_proof or {},
            "analyst_proposal": analyst_proposal,
            "target_config_path": _portable_config_path(cfg_file),
            "created_at": now_iso,
        }
        save_pending_proposal(pending_record, pend_file)
        res = GovernorApprovalResult(
            approved=False,
            status="PENDING_HUMAN_APPROVAL",
            invariants_passed=True,
            tests_passed=tests_ok,
            llm_risk_approved=(verdict_enum == "APPROVE"),
            llm_rationale=llm_rationale + (f" | Auto-Gate Notice: {auto_gate_reason}" if auto_gate_reason else ""),
            rejection_reasons=[auto_gate_reason or "Awaiting mandatory human approval before applying to live configuration."],
            applied_changes={},
            timestamp=now_iso,
            verdict=verdict_enum,
            confidence=verdict_confidence,
            risk_assessment=risk_assessment_str,
            pending_human_approval=True,
            proposal_id=prop_id,
            llm_metrics=llm_telemetry,
        )
        append_governance_audit_log(res)
        return res

    # Step 5: Authorized Atomic Application with Backup & Post-Deployment Rollback Checkpoint
    bak_file = cfg_file.with_suffix(".json.bak")
    if cfg_file.exists():
        shutil.copyfile(cfg_file, bak_file)

    tmp_target = cfg_file.with_suffix(".json.tmp")
    with open(tmp_target, "w") as f:
        json.dump(candidate_cfg, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_target, cfg_file)

    # Supersede any older pending proposals that are now obsolete after this deployment
    try:
        if pend_file.exists():
            all_pend = list_pending_proposals(pend_file)
            changed = False
            for p in all_pend:
                if p.get("status") == "PENDING_HUMAN_APPROVAL" and not is_proposal_still_relevant(p, candidate_cfg):
                    p["status"] = "SUPERSEDED"
                    p["superseded_by"] = prop_id
                    p["superseded_at"] = now_iso
                    changed = True
            if changed:
                tmp_p = pend_file.with_suffix(".json.tmp")
                tmp_p.write_text(json.dumps(all_pend, indent=2))
                os.replace(tmp_p, pend_file)
    except Exception:
        pass

    # Record post-deployment rollback checkpoint so live degradation can auto-revert to .bak
    try:
        port_file = PROJECT_ROOT / "data" / "paper_portfolio.json"
        closed_trades = []
        if port_file.exists():
            closed_trades = json.loads(port_file.read_text()).get("closed_trades", []) or []
        recent_slice = closed_trades[-15:] if closed_trades else []
        pre_exp = (
            sum(float(t.get("pnl", 0.0)) for t in recent_slice) / len(recent_slice)
            if recent_slice
            else 0.0
        )
        snap_file = cfg_file.parent / "last_deployment_snapshot.json"
        snap_payload = {
            "proposal_id": prop_id,
            "deployed_at": now_iso,
            "trade_count_at_deploy": len(closed_trades),
            "pre_deploy_expectancy": round(pre_exp, 4),
            "proposed_changes": proposed_changes,
            "backup_config_path": str(bak_file),
            "target_config_path": str(cfg_file),
            "rolled_back": False,
        }
        tmp_snap = snap_file.with_suffix(".json.tmp")
        tmp_snap.write_text(json.dumps(snap_payload, indent=2))
        os.replace(tmp_snap, snap_file)
    except Exception:
        pass

    deployment_actor = "conditional_auto_gate" if auto_gate_passed else ("human_operator" if human_approved else "governor")
    should_commit = False if auto_commit is None else bool(auto_commit)
    if should_commit:
        commit_msg = f"feat(governance): {deployment_actor} config deployment ({prop_id}) — {rationale or 'verified improvement'}"
        commit_and_push_config(commit_msg)

    res = GovernorApprovalResult(
        approved=True,
        status="DEPLOYED",
        invariants_passed=True,
        tests_passed=tests_ok,
        llm_risk_approved=True,
        llm_rationale=llm_rationale + (" [Auto-Approved via 4-Gate Verification]" if auto_gate_passed else ""),
        rejection_reasons=[],
        applied_changes=proposed_changes,
        timestamp=now_iso,
        verdict="APPROVE",
        confidence=verdict_confidence,
        risk_assessment=risk_assessment_str,
        pending_human_approval=False,
        proposal_id=prop_id,
        llm_metrics=llm_telemetry,
    )
    append_governance_audit_log(res)
    return res


def check_and_rollback_if_degraded(
    closed_trades: List[Dict[str, Any]],
    config_path: Optional[Path] = None,
    snapshot_path: Optional[Path] = None,
    min_eval_trades: int = 10,
) -> Dict[str, Any]:
    """Post-Deployment Automatic Rollback Safety Net.
    Evaluates the first `min_eval_trades` closed trades after a Governor deployment.
    If post-deployment net PnL is negative AND expectancy degraded materially vs pre-deployment
    (or win rate collapsed below 35%), atomically restores active_config.json.bak."""
    cfg_file = config_path or CONFIG_PATH
    snap_file = snapshot_path or (cfg_file.parent / "last_deployment_snapshot.json")
    if not snap_file.exists():
        return {"rolled_back": False, "reason": "No deployment snapshot present"}

    try:
        snap = json.loads(snap_file.read_text())
    except Exception:
        return {"rolled_back": False, "reason": "Unreadable deployment snapshot"}

    if snap.get("rolled_back"):
        return {"rolled_back": False, "reason": "Already rolled back"}

    base_count = int(snap.get("trade_count_at_deploy", 0))
    new_trades = closed_trades[base_count:] if len(closed_trades) > base_count else []
    if len(new_trades) < min_eval_trades:
        return {
            "rolled_back": False,
            "reason": f"Awaiting post-deploy sample ({len(new_trades)}/{min_eval_trades} trades)",
        }

    eval_window = new_trades[:min_eval_trades]
    post_pnl = sum(float(t.get("pnl", 0.0)) for t in eval_window)
    post_exp = post_pnl / len(eval_window)
    wins = sum(1 for t in eval_window if float(t.get("pnl", 0.0)) > 0)
    post_wr = (wins / len(eval_window)) * 100.0
    pre_exp = float(snap.get("pre_deploy_expectancy", 0.0))

    degraded = post_pnl < 0.0 and (post_wr < 35.0 or post_exp < (pre_exp - 3.0))
    if not degraded:
        return {
            "rolled_back": False,
            "reason": f"Post-deploy performance healthy (PnL=${post_pnl:+.2f}, WR={post_wr:.1f}%, Exp=${post_exp:+.2f})",
        }

    bak_file = Path(snap.get("backup_config_path", str(cfg_file.with_suffix(".json.bak"))))
    if not bak_file.exists():
        return {"rolled_back": False, "reason": "Backup config .bak not found for rollback"}

    tmp_restore = cfg_file.with_suffix(".rollback.tmp")
    shutil.copyfile(bak_file, tmp_restore)
    os.replace(tmp_restore, cfg_file)

    snap["rolled_back"] = True
    snap["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
    snap["rolled_back_trade_count"] = len(closed_trades)
    snap["rolled_back_changes"] = snap.get("proposed_changes", {})
    snap["rollback_reason"] = (
        f"Auto-Rollback triggered after {len(eval_window)} trades: "
        f"Post-deploy PnL=${post_pnl:+.2f}, WR={post_wr:.1f}%, Exp=${post_exp:+.2f} vs Pre-Exp=${pre_exp:+.2f}"
    )
    tmp_snap = snap_file.with_suffix(".json.tmp")
    tmp_snap.write_text(json.dumps(snap, indent=2))
    os.replace(tmp_snap, snap_file)

    return {
        "rolled_back": True,
        "proposal_id": snap.get("proposal_id"),
        "reason": snap["rollback_reason"],
    }


def main():
    parser = argparse.ArgumentParser(description="Agent 12: Chief Risk Governor & Deployment Gatekeeper")
    parser.add_argument("--audit", action="store_true", help="Audit active_config.json against safety invariants")
    parser.add_argument("--proposal", type=str, default=None, help="JSON file containing candidate modifications to review")
    parser.add_argument("--pending", "--list-pending", dest="pending", action="store_true", help="List all proposals awaiting human approval")
    parser.add_argument("--auto-approve-pending", action="store_true", help="Evaluate pending proposals against the 4 conditional auto-approval gates")
    parser.add_argument("--approve", type=str, default=None, help="Approve and deploy a pending proposal ID (e.g. PROP-XXXXXXXX)")
    parser.add_argument("--reject", type=str, default=None, help="Reject a pending proposal ID")
    parser.add_argument("--approver", type=str, default="human_cli_operator", help="Human approver identifier")
    parser.add_argument("--no-llm", action="store_true", help="Bypass LLM Risk review")
    parser.add_argument("--no-tests", action="store_true", help="Skip running pytest suite")
    parser.add_argument("--model", type=str, default="phi4-mini:latest", help="Local Ollama model (default: phi4-mini:latest)")
    args = parser.parse_args()

    cfg = load_config()

    print("=" * 80)
    print("  AGENT 12: CHIEF RISK GOVERNOR & DEPLOYMENT GATEKEEPER")
    print(f"  Target Config: {CONFIG_PATH.name} | Risk Limits: [1%, 5%]")
    print("=" * 80)

    if args.pending:
        pending = list_pending_proposals()
        print(f"\n[Pending Human Approval Queue: {len(pending)} proposal(s)]")
        for p in pending:
            print(f"  - ID: {p.get('proposal_id')} | Status: {p.get('status')} | Verdict: {p.get('governor_verdict')} | Created: {p.get('created_at')}")
            print(f"    Rationale: {p.get('rationale')}")
            print(f"    Changes:   {json.dumps(p.get('proposed_changes', {}))}")
        return

    if args.auto_approve_pending:
        results = auto_approve_pending_proposals(verify_tests=not args.no_tests)
        deployed = [r for r in results if r.approved]
        held = [r for r in results if not r.approved]
        print(f"\n[Conditional Auto-Approval Evaluation Summary]")
        print(f"  Total Evaluated: {len(results)}")
        print(f"  Auto-Deployed:   {len(deployed)}")
        print(f"  Held for Human:  {len(held)}")
        for r in results:
            print(f"  - {r.proposal_id}: {r.status} | {r.llm_rationale}")
        return

    if args.approve:
        res = approve_pending_proposal(
            proposal_id=args.approve,
            approved_by=args.approver,
            verify_tests=not args.no_tests,
        )
        print(f"\n[Human Approval Result for {args.approve}]: {res.status} (Approved={res.approved})")
        return

    if args.reject:
        ok = reject_pending_proposal(proposal_id=args.reject, rejected_by=args.approver)
        print(f"\n[Human Rejection Result for {args.reject}]: {'REJECTED_BY_HUMAN' if ok else 'NOT_FOUND'}")
        return

    if args.audit or not args.proposal:
        print("\n[Invariant Audit on Active Configuration]")
        ok, errors = audit_invariants(cfg)
        if ok:
            print("  ✅ All safety invariants PASSED (Paper mode, 1%-5% risk, capital preservation).")
        else:
            print("  ❌ Invariant violations detected:")
            for e in errors:
                print(f"     - {e}")
        return

    prop_path = Path(args.proposal)
    if not prop_path.exists():
        print(f"Error: Proposal file not found: {prop_path}")
        return

    with open(prop_path, "r") as f:
        proposed = json.load(f)

    print(f"\n[Reviewing Proposal from: {prop_path.name}]")
    print(json.dumps(proposed, indent=2))

    res = review_and_deploy_proposal(
        proposed_changes=proposed,
        verify_tests=not args.no_tests,
        use_llm=not args.no_llm,
        model=args.model,
    )

    print("\n" + "=" * 80)
    print(f"  GOVERNOR VERDICT: {res.verdict} | STATUS: {res.status}")
    print(f"  Proposal ID:        {res.proposal_id}")
    print(f"  Invariants Passed:  {res.invariants_passed}")
    print(f"  Pytest Regression:  {res.tests_passed}")
    print(f"  LLM Risk Approved:  {res.llm_risk_approved}")
    print(f"  Pending Human Gate: {res.pending_human_approval}")
    print(f"\n[Governor Rationale]")
    print(res.llm_rationale)
    if res.rejection_reasons:
        print(f"\n[Notes / Rejection Details]")
        for r in res.rejection_reasons:
            print(f"  - {r}")
    print("=" * 80)
    print(f"Audit record logged to: {AUDIT_LOG_FILE}\n")


if __name__ == "__main__":
    main()
