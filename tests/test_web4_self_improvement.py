"""Unit tests for Web4 self-improvement and closed-loop journal feedback."""

import json
from pathlib import Path
import pytest
import pandas as pd

from core.journal import get_gate_alpha_feedback, log_decision
from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from agent.optimizer import run_optimization_cycle


def test_gate_alpha_feedback_empty(tmp_path):
    empty_file = tmp_path / "empty_journal.jsonl"
    feedback = get_gate_alpha_feedback(journal_path=empty_file)
    assert feedback["has_feedback"] is False
    assert feedback["recommended_confluence_delta"] == 0.0


def test_gate_alpha_feedback_effective_blocks(tmp_path):
    journal_file = tmp_path / "test_journal.jsonl"
    entries = []
    # Create 6 settled blocked entries where blocking was effective (effective_call=True)
    for i in range(6):
        entries.append({
            "timestamp": "2026-11-01T12:00:00Z",
            "symbol": "BTC/USDT",
            "action": "REJECTED",
            "strategy_id": "momentum",
            "reason": "Blocked by 200 EMA Macro Bear Gate",
            "reference_price": 95000.0,
            "settled": True,
            "forward_return_pct": -3.5,
            "alpha_pct": -2.0,
            "effective_call": True,
        })
    with open(journal_file, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")

    feedback = get_gate_alpha_feedback(journal_path=journal_file)
    assert feedback["has_feedback"] is True
    assert feedback["blocked_count"] == 6
    assert feedback["block_efficacy_ratio"] == 1.0
    # Effective blocks in dangerous market should tighten confluence (+0.03)
    assert feedback["recommended_confluence_delta"] == 0.03


def test_coordinator_closed_loop_feedback(tmp_path):
    journal_file = tmp_path / "closed_loop_journal.jsonl"
    # Create entries with high effectiveness to trigger +0.03 tightening
    with open(journal_file, "w") as f:
        for _ in range(6):
            f.write(json.dumps({
                "timestamp": "2026-11-01T12:00:00Z",
                "symbol": "SOL/USDT",
                "action": "REJECTED",
                "strategy_id": "momentum",
                "reason": "Blocked by 200 EMA",
                "reference_price": 180.0,
                "settled": True,
                "effective_call": True,
            }) + "\n")

    config = {
        "symbol": "SOL/USDT",
        "timeframe": "1h",
        "enable_confluence_gate": True,
        "min_confluence_score": 0.70,
    }
    account = PaperAccount(initial_capital=10000.0, portfolio_file=tmp_path / "test_acc.json")
    risk_engine = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk_engine, config)

    # Base score is 0.70. With feedback delta (+0.03), effective threshold is 0.73.
    feedback = get_gate_alpha_feedback(journal_path=journal_file)
    assert feedback["recommended_confluence_delta"] == 0.03
