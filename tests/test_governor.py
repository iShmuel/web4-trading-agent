"""Unit tests for Agent 12: Chief Risk Governor & Deployment Gatekeeper."""

import json
import pytest
from pathlib import Path
from agent.governor import (
    audit_invariants,
    review_and_deploy_proposal,
    parse_governor_verdict_json,
    query_llm_risk_review_structured,
    approve_pending_proposal,
    reject_pending_proposal,
    list_pending_proposals,
)


def test_audit_invariants_valid():
    """Verify clean configuration passes all invariant audits."""
    valid_cfg = {
        "execution_mode": "maker",
        "account_risk_pct": 0.02,
        "max_drawdown_limit_pct": 15.0,
        "max_crypto_positions": 2,
        "watchlist": ["BTC/USDT", "ETH/USDT", "NVDA", "AAPL"],
        "asset_profiles": {
            "crypto_micro_swing": {"account_risk_pct": 0.02},
            "stocks_growth": {"account_risk_pct": 0.025},
        },
    }
    ok, violations = audit_invariants(valid_cfg)
    assert ok is True
    assert len(violations) == 0


def test_audit_invariants_rejects_excess_risk():
    """Verify governor catches and rejects risk loosened beyond 5%."""
    bad_cfg = {
        "execution_mode": "maker",
        "account_risk_pct": 0.06,  # 6% exceeds 5% maximum!
        "max_drawdown_limit_pct": 15.0,
        "watchlist": ["BTC/USDT"],
    }
    ok, violations = audit_invariants(bad_cfg)
    assert ok is False
    assert any("exceeds hard bounds" in v for v in violations)


def test_audit_invariants_rejects_excess_crypto_open():
    """Verify governor catches breached crypto concentration guard."""
    bad_cfg = {
        "execution_mode": "maker",
        "account_risk_pct": 0.02,
        "max_crypto_positions": 5,  # 5 exceeds max concurrent 3!
        "watchlist": ["BTC/USDT"],
    }
    ok, violations = audit_invariants(bad_cfg)
    assert ok is False
    assert any("crypto open positions" in v for v in violations)


def test_review_and_deploy_proposal_tmp(tmp_path):
    """Verify review_and_deploy_proposal updates config and writes audit log."""
    base_cfg = {
        "execution_mode": "maker",
        "account_risk_pct": 0.02,
        "max_drawdown_limit_pct": 15.0,
        "max_crypto_positions": 2,
        "watchlist": ["BTC/USDT", "NVDA"],
        "blacklisted_symbols": [],
    }
    cfg_file = tmp_path / "active_config.json"
    cfg_file.write_text(json.dumps(base_cfg))

    proposal = {
        "blacklisted_symbols": ["AVAX/USDT"],
    }

    res = review_and_deploy_proposal(
        proposed_changes=proposal,
        rationale="Prune AVAX drag",
        empirical_proof={"drag_reduction": "$488.00"},
        config_path=cfg_file,
        verify_tests=False,
        use_llm=False,
        human_approved=True,
    )

    assert res.approved is True
    assert res.status == "DEPLOYED"

    with open(cfg_file, "r") as f:
        updated = json.load(f)

    assert "AVAX/USDT" in updated["blacklisted_symbols"]


def test_review_and_deploy_rehabilitation_tmp(tmp_path):
    """Verify governor removes rehabilitated symbols from blacklist and assigns 1% probationary risk weight."""
    base_cfg = {
        "execution_mode": "maker",
        "account_risk_pct": 0.02,
        "max_drawdown_limit_pct": 15.0,
        "max_crypto_positions": 2,
        "watchlist": ["BTC/USDT", "NVDA"],
        "blacklisted_symbols": ["SOL/USDT", "ETH/USDT"],
    }
    cfg_file = tmp_path / "active_config.json"
    cfg_file.write_text(json.dumps(base_cfg))

    proposal = {
        "rehabilitated_symbols": ["SOL/USDT"],
        "watchlist": ["BTC/USDT", "NVDA", "SOL/USDT"],
    }

    res = review_and_deploy_proposal(
        proposed_changes=proposal,
        rationale="Rehabilitate SOL/USDT to probationary status",
        empirical_proof={"rehabilitated": ["SOL/USDT"]},
        config_path=cfg_file,
        verify_tests=False,
        use_llm=False,
        human_approved=True,
    )

    assert res.approved is True
    assert res.status == "DEPLOYED"

    with open(cfg_file, "r") as f:
        updated = json.load(f)

    # SOL/USDT should be removed from blacklisted_symbols and present in watchlist
    assert "SOL/USDT" not in updated["blacklisted_symbols"]
    assert "ETH/USDT" in updated["blacklisted_symbols"]
    assert "SOL/USDT" in updated["watchlist"]
    # Should receive 1.0% (0.01) probationary risk weight
    assert updated["symbol_risk_weights"]["SOL/USDT"] == 0.01


def test_audit_invariants_rejects_invalid_symbol_risk_weights():
    """Verify governor catches and rejects per-symbol risk weights outside [1%, 5%]."""
    bad_cfg = {
        "execution_mode": "maker",
        "account_risk_pct": 0.02,
        "max_drawdown_limit_pct": 15.0,
        "watchlist": ["BTC/USDT", "SOL/USDT"],
        "symbol_risk_weights": {
            "BTC/USDT": 0.02,
            "SOL/USDT": 0.08,  # 8% breaches 5% max bound!
        },
    }
    ok, violations = audit_invariants(bad_cfg)
    assert ok is False
    assert any("SOL/USDT" in v and "exceeds hard bounds" in v for v in violations)


def test_review_and_deploy_proposal_auto_commits_when_enabled(tmp_path, monkeypatch):
    """Verify human-approved governance proposals commit and push when auto_commit is enabled."""
    committed_messages = []
    monkeypatch.setattr("agent.governor.commit_and_push_config", lambda msg: committed_messages.append(msg) or True)

    base_cfg = {
        "execution_mode": "maker",
        "account_risk_pct": 0.02,
        "max_drawdown_limit_pct": 15.0,
        "max_crypto_positions": 2,
        "watchlist": ["BNB/USDT", "NVDA"],
        "blacklisted_symbols": [],
    }
    cfg_file = tmp_path / "active_config.json"
    cfg_file.write_text(json.dumps(base_cfg))

    res = review_and_deploy_proposal(
        proposed_changes={"watchlist": ["BNB/USDT", "TRX/USDT", "NVDA", "PLUG"]},
        rationale="Add TRX/USDT and PLUG from 90d discovery audit",
        config_path=cfg_file,
        verify_tests=False,
        use_llm=False,
        auto_commit=True,
        human_approved=True,
    )

    assert res.approved is True
    assert len(committed_messages) == 1
    assert "Add TRX/USDT and PLUG" in committed_messages[0]


def test_governor_verdict_json_parsing_all_required_cases(monkeypatch, tmp_path):
    """Verify all 11 required Governor parsing, fail-closed, bounds, and pending approval cases."""
    # 1. "APPROVE"
    ok1, v1, d1 = parse_governor_verdict_json(json.dumps({
        "verdict": "APPROVE",
        "reason": "Empirical Sharpe improved with lower drawdown.",
        "risk_assessment": "Within 1%-5% risk bounds.",
        "confidence": 0.91,
    }))
    assert ok1 is True and v1 == "APPROVE" and d1["confidence"] == 0.91

    # 2. "DISAPPROVE" (previously matched "APPROVE" due to substring bug!)
    ok2, v2, d2 = parse_governor_verdict_json(json.dumps({
        "verdict": "DISAPPROVE",
        "reason": "Increases tail risk.",
        "risk_assessment": "Unhedged exposure.",
        "confidence": 0.88,
    }))
    assert ok2 is False and v2 == "DISAPPROVE"

    # 3. "NEEDS_HUMAN_REVIEW"
    ok3, v3, d3 = parse_governor_verdict_json(json.dumps({
        "verdict": "NEEDS_HUMAN_REVIEW",
        "reason": "Marginal improvement on small sample.",
        "risk_assessment": "Requires human operator sign-off.",
        "confidence": 0.55,
    }))
    assert ok3 is False and v3 == "NEEDS_HUMAN_REVIEW"

    # 4. "NOT APPROVED" -> must fail closed as DISAPPROVE
    ok4, v4, d4 = parse_governor_verdict_json(json.dumps({
        "verdict": "NOT APPROVED",
        "reason": "Rejected.",
        "risk_assessment": "High risk.",
        "confidence": 0.80,
    }))
    assert ok4 is False and v4 == "DISAPPROVE"
    assert d4["parse_error"] == "invalid_verdict_enum"

    # 5. "DO NOT APPROVE" -> must fail closed as DISAPPROVE
    ok5, v5, d5 = parse_governor_verdict_json(json.dumps({
        "verdict": "DO NOT APPROVE",
        "reason": "Do not deploy.",
        "risk_assessment": "Unsafe.",
        "confidence": 0.95,
    }))
    assert ok5 is False and v5 == "DISAPPROVE"
    assert d5["parse_error"] == "invalid_verdict_enum"

    # 6. Malformed JSON -> must fail closed as DISAPPROVE
    ok6, v6, d6 = parse_governor_verdict_json("VERDICT: APPROVE\nRATIONALE: Looks good")
    assert ok6 is False and v6 == "DISAPPROVE"
    assert d6["parse_error"] == "malformed_json"

    # 7. Empty response -> must fail closed as DISAPPROVE
    ok7, v7, d7 = parse_governor_verdict_json("   ")
    assert ok7 is False and v7 == "DISAPPROVE"
    assert d7["parse_error"] == "empty_response"

    # 8. Timeout / connection failure -> must fail closed as DISAPPROVE
    monkeypatch.setattr(
        "agent.governor.query_local_llm_with_metrics",
        lambda **kwargs: {
            "response": None,
            "latency_ms": 15000.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "success": False,
            "error": "TimeoutError: local Ollama timed out",
        },
    )
    ok8, v8, d8, _ = query_llm_risk_review_structured(
        proposed_diff={"adx_chop_threshold": 22.0},
        base_config={"account_risk_pct": 0.02},
    )
    assert ok8 is False and v8 == "DISAPPROVE"
    assert d8["parse_error"] == "llm_unavailable"

    # 9. Missing fields -> must fail closed as DISAPPROVE
    ok9, v9, d9 = parse_governor_verdict_json(json.dumps({
        "verdict": "APPROVE",
        "reason": "Missing risk_assessment and confidence",
    }))
    assert ok9 is False and v9 == "DISAPPROVE"
    assert d9["parse_error"] == "missing_fields"

    # 10. Invalid parameter bounds -> rejected by audit_invariants
    bad_bounds_cfg = {
        "execution_mode": "maker",
        "account_risk_pct": 0.02,
        "max_drawdown_limit_pct": 15.0,
        "watchlist": ["BTC/USDT"],
        "atr_multiplier": 0.2,  # Below minimum 1.0 bound!
        "rsi_oversold": 75.0,   # Above maximum 45.0 bound!
    }
    ok10, violations10 = audit_invariants(bad_bounds_cfg)
    assert ok10 is False
    assert any("atr_multiplier" in v for v in violations10)
    assert any("rsi_oversold" in v for v in violations10)

    # 11. Pending human approval workflow:
    # By default (require_human_approval=True, human_approved=False), active_config.json is NOT overwritten
    # until approve_pending_proposal() is explicitly called by a human.
    cfg_file = tmp_path / "active_config.json"
    pend_file = tmp_path / "pending_proposals.json"
    base_cfg = {
        "execution_mode": "maker",
        "account_risk_pct": 0.02,
        "max_drawdown_limit_pct": 15.0,
        "max_crypto_positions": 2,
        "adx_chop_threshold": 20.0,
        "watchlist": ["BTC/USDT"],
        "blacklisted_symbols": [],
    }
    cfg_file.write_text(json.dumps(base_cfg))

    pending_res = review_and_deploy_proposal(
        proposed_changes={"adx_chop_threshold": 22.0},
        rationale="Increase ADX chop filter to 22.0",
        config_path=cfg_file,
        pending_proposals_path=pend_file,
        verify_tests=False,
        use_llm=False,
        proposal_id="PROP-TEST-HITL-01",
    )
    assert pending_res.approved is False
    assert pending_res.status == "PENDING_HUMAN_APPROVAL"
    assert pending_res.pending_human_approval is True
    assert json.loads(cfg_file.read_text())["adx_chop_threshold"] == 20.0  # Unchanged!

    queued = list_pending_proposals(pend_file)
    assert len(queued) == 1
    assert queued[0]["proposal_id"] == "PROP-TEST-HITL-01"
    assert queued[0]["status"] == "PENDING_HUMAN_APPROVAL"

    # Now human explicitly approves the pending proposal
    deploy_res = approve_pending_proposal(
        proposal_id="PROP-TEST-HITL-01",
        approved_by="shmuel_berger",
        config_path=cfg_file,
        pending_path=pend_file,
        verify_tests=False,
    )
    assert deploy_res.approved is True
    assert deploy_res.status == "DEPLOYED"
    assert json.loads(cfg_file.read_text())["adx_chop_threshold"] == 22.0  # Applied after human approval!
    queued_after = list_pending_proposals(pend_file)
    assert queued_after[0]["status"] == "DEPLOYED"
    assert queued_after[0]["approved_by"] == "shmuel_berger"



