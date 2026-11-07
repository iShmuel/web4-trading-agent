"""Unit tests for Agent 9: Autonomous Alpha Scanner & Market Discovery."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np

from agent.asset_selector import (
    AssetEvaluation,
    calculate_composite_alpha_score,
    evaluate_candidate_asset,
    build_selection_summary,
    apply_recommendations_to_config,
    discover_dynamic_crypto_universe,
    discover_dynamic_stock_universe,
)


def test_composite_alpha_score_profitable():
    """Verify CAS rewards high win rate, high profit factor, and low drawdown."""
    score = calculate_composite_alpha_score(
        net_return_pct=5.0,
        win_rate_pct=65.0,
        profit_factor=1.8,
        max_drawdown_pct=3.0,
        sharpe_ratio=2.1,
        is_return_pct=4.0,
        oos_return_pct=4.5,
    )
    assert score > 15.0


def test_composite_alpha_score_penalizes_drag():
    """Verify CAS assigns a negative penalty to losing or choppy assets."""
    score = calculate_composite_alpha_score(
        net_return_pct=-3.5,
        win_rate_pct=42.0,
        profit_factor=0.7,
        max_drawdown_pct=8.0,
        sharpe_ratio=-1.2,
        is_return_pct=-2.0,
        oos_return_pct=-1.5,
    )
    assert score < -10.0


def test_composite_alpha_score_overfitting_penalty():
    """Verify CAS penalizes strategies that fail out-of-sample."""
    is_stable = calculate_composite_alpha_score(
        net_return_pct=4.0,
        win_rate_pct=60.0,
        profit_factor=1.5,
        max_drawdown_pct=4.0,
        sharpe_ratio=1.5,
        is_return_pct=4.0,
        oos_return_pct=3.8,
    )
    overfitted = calculate_composite_alpha_score(
        net_return_pct=4.0,
        win_rate_pct=60.0,
        profit_factor=1.5,
        max_drawdown_pct=4.0,
        sharpe_ratio=1.5,
        is_return_pct=8.0,
        oos_return_pct=-1.0,  # Negative out-of-sample
    )
    assert is_stable > overfitted


def test_discover_dynamic_crypto_universe_filters_stablecoins():
    """Verify that dynamic crypto discovery discards stablecoins and ranks by volume."""
    mock_tickers = {
        "BTC/USDT": {"quoteVolume": 500_000_000, "last": 70000},
        "USDC/USDT": {"quoteVolume": 900_000_000, "last": 1.0},  # Stablecoin - should be ignored!
        "FDUSD/USDT": {"quoteVolume": 400_000_000, "last": 1.0},  # Stablecoin - should be ignored!
        "NEAR/USDT": {"quoteVolume": 80_000_000, "last": 3.5},
        "BTCUP/USDT": {"quoteVolume": 50_000_000, "last": 15.0},  # Leveraged token - ignored!
        "TINY/USDT": {"quoteVolume": 50_000, "last": 0.01},  # Under min volume!
    }

    with patch("ccxt.binance") as mock_ex_class:
        mock_ex = MagicMock()
        mock_ex.fetch_tickers.return_value = mock_tickers
        mock_ex_class.return_value = mock_ex

        discovered = discover_dynamic_crypto_universe(min_volume_usd=10_000_000, top_n=5)

    assert "BTC/USDT" in discovered
    assert "NEAR/USDT" in discovered
    assert "USDC/USDT" not in discovered
    assert "FDUSD/USDT" not in discovered
    assert "BTCUP/USDT" not in discovered
    assert "TINY/USDT" not in discovered


def test_build_selection_summary_limits():
    """Verify that portfolio universe limits (max_crypto and max_stocks) are enforced."""
    dummy_evals = [
        AssetEvaluation(
            symbol=f"CRYPTO{i}/USDT",
            asset_type="crypto",
            primary_strategy="MOMENTUM",
            days_evaluated=90,
            total_bars=2000,
            adx_trend_ratio=40.0,
            volatility_atr_pct=2.5,
            momentum_return_pct=5.0,
            mean_reversion_return_pct=1.0,
            in_sample_return_pct=4.0,
            out_of_sample_return_pct=3.5,
            net_return_pct=5.0 - i,
            net_pnl=500.0,
            win_rate_pct=65.0,
            profit_factor=1.5,
            max_drawdown_pct=4.0,
            sharpe_ratio=1.8,
            payoff_ratio=1.2,
            total_trades=20,
            winning_trades=13,
            losing_trades=7,
            composite_alpha_score=20.0 - i,
            tier="ELITE_ALPHA" if i < 3 else "VIABLE_ALPHA",
            recommendation="ADD_TO_WATCHLIST",
            rationale="Strong performer",
        )
        for i in range(10)
    ]
    dummy_evals.append(
        AssetEvaluation(
            symbol="TOXIC/USDT",
            asset_type="crypto",
            primary_strategy="MEAN_REVERSION",
            days_evaluated=90,
            total_bars=2000,
            adx_trend_ratio=10.0,
            volatility_atr_pct=3.5,
            momentum_return_pct=-8.0,
            mean_reversion_return_pct=-5.0,
            in_sample_return_pct=-3.0,
            out_of_sample_return_pct=-4.0,
            net_return_pct=-5.0,
            net_pnl=-500.0,
            win_rate_pct=40.0,
            profit_factor=0.6,
            max_drawdown_pct=12.0,
            sharpe_ratio=-1.5,
            payoff_ratio=0.7,
            total_trades=15,
            winning_trades=6,
            losing_trades=9,
            composite_alpha_score=-25.0,
            tier="TOXIC_DRAG",
            recommendation="BLACKLIST_OR_PRUNE",
            rationale="Heavy loss",
        )
    )

    summary = build_selection_summary(dummy_evals, max_crypto=3, max_stocks=5)
    assert len(summary["selected_crypto"]) == 3
    assert "TOXIC/USDT" in summary["recommended_blacklist"]
    assert "TOXIC/USDT" not in summary["recommended_watchlist"]


def test_apply_recommendations_safe_backup(tmp_path):
    """Verify that configuration updates produce a backup and respect schema."""
    fake_cfg = {
        "execution_mode": "maker",
        "account_risk_pct": 0.02,
        "max_drawdown_limit_pct": 15.0,
        "max_crypto_positions": 2,
        "watchlist": ["BTC/USDT"],
        "blacklisted_symbols": [],
    }
    cfg_path = tmp_path / "active_config.json"
    with open(cfg_path, "w") as f:
        json.dump(fake_cfg, f)

    new_watchlist = ["BTC/USDT", "NEAR/USDT", "NVDA"]
    new_blacklist = ["OLD/USDT"]

    success = apply_recommendations_to_config(
        recommended_watchlist=new_watchlist,
        recommended_blacklist=new_blacklist,
        config_path=cfg_path,
        verify_tests=False,
    )

    assert success is True
    assert (tmp_path / "active_config.json.bak").exists()

    with open(cfg_path, "r") as f:
        updated = json.load(f)

    assert updated["watchlist"] == new_watchlist
    assert "OLD/USDT" in updated["blacklisted_symbols"]


def test_build_selection_summary_proven_pruning():
    """Verify that assets proven non-profitable are pruned and added to blacklist."""
    profitable_crypto = AssetEvaluation(
        symbol="WIN/USDT",
        asset_type="crypto",
        primary_strategy="MOMENTUM",
        days_evaluated=90,
        total_bars=2000,
        adx_trend_ratio=40.0,
        volatility_atr_pct=2.5,
        momentum_return_pct=5.0,
        mean_reversion_return_pct=1.0,
        in_sample_return_pct=4.0,
        out_of_sample_return_pct=3.5,
        net_return_pct=4.5,
        net_pnl=450.0,
        win_rate_pct=75.0,
        profit_factor=2.0,
        max_drawdown_pct=3.0,
        sharpe_ratio=2.5,
        payoff_ratio=1.5,
        total_trades=20,
        winning_trades=15,
        losing_trades=5,
        composite_alpha_score=25.0,
        tier="ELITE_ALPHA",
        recommendation="ADD_TO_WATCHLIST",
        rationale="Proven winner",
    )
    losing_stock = AssetEvaluation(
        symbol="DRAG",
        asset_type="stock",
        primary_strategy="MOMENTUM",
        days_evaluated=90,
        total_bars=440,
        adx_trend_ratio=15.0,
        volatility_atr_pct=3.0,
        momentum_return_pct=-3.5,
        mean_reversion_return_pct=-2.0,
        in_sample_return_pct=-1.5,
        out_of_sample_return_pct=-2.0,
        net_return_pct=-3.5,
        net_pnl=-350.0,
        win_rate_pct=30.0,
        profit_factor=0.5,
        max_drawdown_pct=8.0,
        sharpe_ratio=-1.1,
        payoff_ratio=0.8,
        total_trades=10,
        winning_trades=3,
        losing_trades=7,
        composite_alpha_score=-15.0,
        tier="TOXIC_DRAG",
        recommendation="BLACKLIST_OR_PRUNE",
        rationale="Proven loser",
    )

    summary = build_selection_summary(
        [profitable_crypto, losing_stock],
        max_crypto=5,
        max_stocks=10,
        existing_blacklist=["PREV_BAD"],
    )

    assert "WIN/USDT" in summary["recommended_watchlist"]
    assert "DRAG" not in summary["recommended_watchlist"]
    assert "DRAG" in summary["recommended_blacklist"]
    assert "PREV_BAD" in summary["recommended_blacklist"]
    assert "WIN/USDT" in summary["proven_profitable_symbols"]
    assert "DRAG" in summary["pruned_drag_symbols"]


def test_audit_and_rehabilitate_blacklist(monkeypatch):
    """Verify that blacklisted assets with recovering alpha graduate to parole."""
    from agent.asset_selector import audit_and_rehabilitate_blacklist, print_rehabilitation_report

    mock_cfg = {
        "blacklisted_symbols": ["RECOVERING/USDT", "STILL_BAD/USDT"],
    }

    def mock_evaluate(symbol, **kwargs):
        if symbol == "RECOVERING/USDT":
            return AssetEvaluation(
                symbol="RECOVERING/USDT",
                asset_type="crypto",
                primary_strategy="MOMENTUM",
                days_evaluated=60,
                total_bars=1000,
                adx_trend_ratio=35.0,
                volatility_atr_pct=2.5,
                momentum_return_pct=6.5,
                mean_reversion_return_pct=1.0,
                in_sample_return_pct=4.0,
                out_of_sample_return_pct=4.5,
                net_return_pct=5.5,
                net_pnl=550.0,
                win_rate_pct=65.0,
                profit_factor=1.8,
                max_drawdown_pct=5.0,
                sharpe_ratio=2.1,
                payoff_ratio=1.4,
                total_trades=15,
                winning_trades=10,
                losing_trades=5,
                composite_alpha_score=20.0,
                tier="ELITE_ALPHA",
                recommendation="ADD_TO_WATCHLIST",
                rationale="Alpha recovered",
            )
        else:
            return AssetEvaluation(
                symbol="STILL_BAD/USDT",
                asset_type="crypto",
                primary_strategy="MOMENTUM",
                days_evaluated=60,
                total_bars=1000,
                adx_trend_ratio=15.0,
                volatility_atr_pct=2.0,
                momentum_return_pct=-4.0,
                mean_reversion_return_pct=-1.5,
                in_sample_return_pct=-2.0,
                out_of_sample_return_pct=-2.5,
                net_return_pct=-4.0,
                net_pnl=-400.0,
                win_rate_pct=30.0,
                profit_factor=0.6,
                max_drawdown_pct=14.0,
                sharpe_ratio=-1.0,
                payoff_ratio=0.7,
                total_trades=10,
                winning_trades=3,
                losing_trades=7,
                composite_alpha_score=-10.0,
                tier="TOXIC_DRAG",
                recommendation="BLACKLIST_OR_PRUNE",
                rationale="Persistent drag",
            )

    monkeypatch.setattr("agent.asset_selector.evaluate_candidate_asset", mock_evaluate)

    report = audit_and_rehabilitate_blacklist(
        days=60,
        config=mock_cfg,
    )

    assert "RECOVERING/USDT" in report["rehabilitated_symbols"]
    assert "STILL_BAD/USDT" in report["quarantine_symbols"]
    assert report["rehabilitated_count"] == 1
    assert report["quarantine_count"] == 1

    # Ensure report printing executes without exception
    print_rehabilitation_report(report)
