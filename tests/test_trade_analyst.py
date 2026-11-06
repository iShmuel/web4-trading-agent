"""Unit tests for Agent 10: Post-Trade Forensic Analyst."""

import pytest
from pathlib import Path
from agent.trade_analyst import perform_statistical_audit, run_trade_forensic_analysis


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
