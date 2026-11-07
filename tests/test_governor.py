"""Unit tests for Agent 12: Chief Risk Governor & Deployment Gatekeeper."""

import json
import pytest
from pathlib import Path
from agent.governor import audit_invariants, review_and_deploy_proposal


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
    """Verify approved governance proposals automatically commit and push when auto_commit is enabled."""
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
    )

    assert res.approved is True
    assert len(committed_messages) == 1
    assert "Add TRX/USDT and PLUG" in committed_messages[0]


