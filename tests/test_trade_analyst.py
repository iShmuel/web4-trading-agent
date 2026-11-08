"""Unit tests for Agent 10: Post-Trade Forensic Analyst."""

import pytest
from pathlib import Path
from agent.trade_analyst import (
    perform_statistical_audit,
    run_trade_forensic_analysis,
    validate_analyst_proposal_schema,
    generate_llm_analyst_proposals,
)


def test_statistical_audit_metrics():
    """Verify statistical audit calculations for win rate, payoff ratio, and PnL."""
    trades = [
        {"symbol": "NEAR/USDT", "strategy_id": "momentum", "pnl": 50.0, "return_pct": 5.0, "fees": 0.5, "exit_reason": "Take Profit"},
        {"symbol": "NEAR/USDT", "strategy_id": "momentum", "pnl": 30.0, "return_pct": 3.0, "fees": 0.5, "exit_reason": "Take Profit"},
        {"symbol": "SOL/USDT", "strategy_id": "momentum", "pnl": -40.0, "return_pct": -4.0, "fees": 0.5, "exit_reason": "Stop Loss Hit"},
        {"symbol": "AAPL", "strategy_id": "mean_reversion", "pnl": 20.0, "return_pct": 2.0, "fees": 0.2, "exit_reason": "Target Hit"},
    ]

    stats = perform_statistical_audit(trades)

    assert stats["total_trades"] == 4
    assert stats["winning_trades"] == 3
    assert stats["losing_trades"] == 1
    assert stats["win_rate_pct"] == 75.0
    assert stats["total_pnl"] == 60.0
    assert stats["momentum_pnl"] == 40.0
    assert stats["mr_pnl"] == 20.0
    assert stats["profit_factor"] == 2.5  # 100 / 40
    assert stats["avg_win"] == round((50 + 30 + 20) / 3, 2)
    assert stats["avg_loss"] == 40.0
    assert stats["exit_reasons"]["Stop Loss Hit"] == 1


def test_trade_forensic_analysis_report(tmp_path):
    """Verify end-to-end report generation with deterministic fallback."""
    fake_portfolio = {
        "closed_trades": [
            {"symbol": "SUI/USDT", "strategy_id": "momentum", "pnl": 100.0, "fees": 1.0, "exit_reason": "Take Profit"},
            {"symbol": "ETH/USDT", "strategy_id": "mean_reversion", "pnl": -80.0, "fees": 1.0, "exit_reason": "Stop Loss Hit"},
        ]
    }
    port_file = tmp_path / "paper_portfolio.json"
    port_file.write_text(json_dump := __import__("json").dumps(fake_portfolio))
    rep_file = tmp_path / "report.json"

    summary = run_trade_forensic_analysis(
        trade_window=10,
        portfolio_path=port_file,
        output_path=rep_file,
        use_llm=False,  # deterministic fast test
    )

    assert summary.total_trades == 2
    assert summary.winning_trades == 1
    assert summary.losing_trades == 1
    assert summary.total_realized_pnl == 20.0
    assert rep_file.exists()


def test_analyst_proposal_schema_validation_and_llm_generation(monkeypatch):
    """Verify strict 11-field Analyst proposal schema validation and LLM hypothesis generation."""
    valid_proposal = {
        "proposal_id": "PROP-LLM-001",
        "strategy_id": "momentum",
        "strategy_name": "Donchian Momentum Breakout",
        "hypothesis": "Raise breakout_lookback from 24 to 30 to reduce chop false breakouts.",
        "proposed_changes": {"breakout_lookback": 30},
        "supporting_evidence": {"win_rate_pct": 46.5, "profit_factor": 0.94},
        "expected_benefit": "Higher profit factor and lower fee churn.",
        "identified_risks": ["Slightly later entry on sharp V-reversals."],
        "evaluation_metrics": {"target_profit_factor": 1.15},
        "confidence": 0.84,
        "reasoning": "Losses concentrated in 18-24h false range expansions.",
    }
    ok, errs, norm = validate_analyst_proposal_schema(valid_proposal)
    assert ok is True
    assert errs == []
    assert norm["proposal_id"] == "PROP-LLM-001"

    # Missing required fields must fail validation
    bad_proposal = {"proposal_id": "PROP-BAD", "strategy_id": "momentum"}
    ok_bad, errs_bad, norm_bad = validate_analyst_proposal_schema(bad_proposal)
    assert ok_bad is False
    assert len(errs_bad) > 0
    assert norm_bad is None

    # Mock LLM output with valid JSON schema
    monkeypatch.setattr(
        "agent.trade_analyst.query_local_llm_with_metrics",
        lambda **kwargs: {
            "response": __import__("json").dumps({
                "diagnosis": "Momentum suffered range whipsaws while Mean Reversion captured positive expectancy.",
                "proposals": [valid_proposal],
            }),
            "latency_ms": 412.5,
            "prompt_tokens": 220,
            "completion_tokens": 145,
            "total_tokens": 365,
            "success": True,
            "error": None,
        },
    )
    stats = perform_statistical_audit([
        {"symbol": "BTC/USDT", "strategy_id": "momentum", "pnl": -45.0, "fees": 1.2, "exit_reason": "ATR Trailing Stop Hit"},
        {"symbol": "BNB/USDT", "strategy_id": "mean_reversion", "pnl": 65.0, "fees": 1.0, "exit_reason": "Mean Reversion Target"},
    ])
    res = generate_llm_analyst_proposals(stats, [
        {"symbol": "BTC/USDT", "strategy_id": "momentum", "pnl": -45.0, "exit_reason": "ATR Trailing Stop Hit"}
    ])
    assert res["schema_valid"] is True
    assert res["fallback_used"] is False
    assert len(res["proposals"]) == 1
    assert res["proposals"][0]["proposal_id"] == "PROP-LLM-001"

