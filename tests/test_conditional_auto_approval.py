"""Unit tests for the 4-Gate Conditional Auto-Approval system.

Verifies:
1. Gate 1: Safety Invariants (1%-5% risk, Paper mode, Max DD limits).
2. Gate 2: Pytest regression gate (0 errors).
3. Gate 3: Governor confidence (100% APPROVE).
4. Gate 4: Strict Alpha Superiority / Non-Degradation Gate (OOS return, Sharpe, MaxDD).
"""

import json
from pathlib import Path
import pytest

from agent.governor import (
    evaluate_conditional_auto_approval_gates,
    review_and_deploy_proposal,
    auto_approve_pending_proposals,
    load_config,
)


@pytest.fixture
def test_env(tmp_path):
    base_cfg = {
        "watchlist": ["BTC/USDT", "NVDA"],
        "execution_mode": "paper",
        "account_risk_pct": 0.02,
        "max_concurrent_positions": 5,
        "max_crypto_positions": 2,
        "max_portfolio_drawdown_pct": 15.0,
        "enable_conditional_auto_approval": True,
        "auto_approve_min_oos_return_delta": 0.0,
        "auto_approve_min_oos_sharpe_delta": 0.0,
        "auto_approve_max_drawdown_tolerance_pct": 0.5,
        "breakout_lookback": 24,
    }
    cfg_file = tmp_path / "active_config.json"
    with open(cfg_file, "w") as f:
        json.dump(base_cfg, f, indent=2)

    pend_file = tmp_path / "pending_proposals.json"
    with open(pend_file, "w") as f:
        json.dump([], f)

    return {"cfg_file": cfg_file, "pend_file": pend_file, "base_cfg": base_cfg}


def test_gate_1_invariants_failure(test_env):
    candidate_cfg = dict(test_env["base_cfg"])
    candidate_cfg["account_risk_pct"] = 0.08  # Illegal 8% risk

    passed, reason, details = evaluate_conditional_auto_approval_gates(
        candidate_cfg=candidate_cfg,
        base_cfg=test_env["base_cfg"],
        empirical_proof={"test_return_delta": 2.5, "test_sharpe_delta": 0.4},
        invariants_passed=False,
        tests_passed=True,
        governor_verdict="APPROVE",
        governor_confidence=1.0,
    )

    assert passed is False
    assert "Gate 1 Failed" in reason
    assert details["gate_1_invariants"] is False


def test_gate_2_tests_failure(test_env):
    passed, reason, details = evaluate_conditional_auto_approval_gates(
        candidate_cfg=test_env["base_cfg"],
        base_cfg=test_env["base_cfg"],
        empirical_proof={"test_return_delta": 2.5, "test_sharpe_delta": 0.4},
        invariants_passed=True,
        tests_passed=False,
        governor_verdict="APPROVE",
        governor_confidence=1.0,
    )

    assert passed is False
    assert "Gate 2 Failed" in reason
    assert details["gate_2_tests"] is False


def test_gate_3_governor_confidence_failure(test_env):
    passed, reason, details = evaluate_conditional_auto_approval_gates(
        candidate_cfg=test_env["base_cfg"],
        base_cfg=test_env["base_cfg"],
        empirical_proof={"test_return_delta": 2.5, "test_sharpe_delta": 0.4},
        invariants_passed=True,
        tests_passed=True,
        governor_verdict="APPROVE",
        governor_confidence=0.75,  # Less than 100% confidence
    )

    assert passed is False
    assert "Gate 3 Failed: Governor verdict is APPROVE (confidence=0.75 < 1.00)" in reason


def test_gate_4_sure_improvement_negative_return(test_env):
    passed, reason, details = evaluate_conditional_auto_approval_gates(
        candidate_cfg=test_env["base_cfg"],
        base_cfg=test_env["base_cfg"],
        empirical_proof={"test_return_delta": -1.2, "test_sharpe_delta": 0.1},
        invariants_passed=True,
        tests_passed=True,
        governor_verdict="APPROVE",
        governor_confidence=1.0,
    )

    assert passed is False
    assert "Gate 4 Failed: Out-of-sample return delta (-1.20%) did not meet minimum threshold" in reason
    assert details["gate_4_superiority"] is False


def test_gate_4_sure_improvement_degraded_sharpe(test_env):
    passed, reason, details = evaluate_conditional_auto_approval_gates(
        candidate_cfg=test_env["base_cfg"],
        base_cfg=test_env["base_cfg"],
        empirical_proof={
            "test_return_delta": 1.5,
            "test_sharpe": 1.50,
            "base_test_sharpe": 2.10,
        },
        invariants_passed=True,
        tests_passed=True,
        governor_verdict="APPROVE",
        governor_confidence=1.0,
    )

    assert passed is False
    assert "Gate 4 Failed: Candidate Out-of-Sample Sharpe (1.50) degraded below baseline (2.10)" in reason


def test_gate_4_sure_improvement_expanded_drawdown(test_env):
    passed, reason, details = evaluate_conditional_auto_approval_gates(
        candidate_cfg=test_env["base_cfg"],
        base_cfg=test_env["base_cfg"],
        empirical_proof={
            "test_return_delta": 3.0,
            "test_sharpe_delta": 0.3,
            "test_max_dd": 11.2,
            "base_test_max_dd": 6.0,  # 11.2 > 6.0 + 0.5
        },
        invariants_passed=True,
        tests_passed=True,
        governor_verdict="APPROVE",
        governor_confidence=1.0,
    )

    assert passed is False
    assert "exceeds baseline MaxDD" in reason


def test_all_4_gates_pass_end_to_end_auto_deploys(test_env):
    cfg_file = test_env["cfg_file"]
    pend_file = test_env["pend_file"]

    proposed_changes = {"breakout_lookback": 36}
    empirical_proof = {
        "test_return_delta": +2.85,
        "test_sharpe_delta": +0.42,
        "test_sharpe": 2.65,
        "base_test_sharpe": 2.23,
        "test_max_dd": 5.4,
        "base_test_max_dd": 5.8,
    }

    res = review_and_deploy_proposal(
        proposed_changes=proposed_changes,
        rationale="Tested walk-forward superior breakout lookback.",
        empirical_proof=empirical_proof,
        config_path=cfg_file,
        verify_tests=False,
        use_llm=False,
        auto_commit=False,
        require_human_approval=True,
        human_approved=False,
        pending_proposals_path=pend_file,
    )

    # Must be auto-deployed through 4-gate verification
    assert res.approved is True
    assert res.status == "DEPLOYED"
    assert "Auto-Approved via 4-Gate Verification" in res.llm_rationale

    # Active config must be updated
    updated_cfg = load_config(cfg_file)
    assert updated_cfg["breakout_lookback"] == 36


def test_auto_approval_disabled_holds_passing_proposal_for_human(test_env):
    cfg_file = test_env["cfg_file"]
    pend_file = test_env["pend_file"]

    # Disable auto-approval in config
    cfg = load_config(cfg_file)
    cfg["enable_conditional_auto_approval"] = False
    with open(cfg_file, "w") as f:
        json.dump(cfg, f, indent=2)

    proposed_changes = {"breakout_lookback": 40}
    empirical_proof = {
        "test_return_delta": +3.0,
        "test_sharpe_delta": +0.5,
    }

    res = review_and_deploy_proposal(
        proposed_changes=proposed_changes,
        rationale="Candidate beats baseline but auto-approval is disabled.",
        empirical_proof=empirical_proof,
        config_path=cfg_file,
        verify_tests=False,
        use_llm=False,
        auto_commit=False,
        require_human_approval=True,
        human_approved=False,
        pending_proposals_path=pend_file,
    )

    # Must be held in PENDING_HUMAN_APPROVAL
    assert res.approved is False
    assert res.status == "PENDING_HUMAN_APPROVAL"
    assert res.pending_human_approval is True

    # Active config remains unchanged
    current_cfg = load_config(cfg_file)
    assert current_cfg["breakout_lookback"] == 24
