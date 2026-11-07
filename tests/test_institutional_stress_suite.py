"""Tests for Institutional Quant Stress Suite: DSR, CPCV, Slippage Multiplier, Underwater, and Crisis Shocks."""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

from core.institutional_stress_tester import (
    calculate_psr_and_dsr,
    run_cpcv_split,
    run_slippage_multiplier_stress,
    calculate_underwater_and_streak_metrics,
    run_crisis_stress_replay,
    StressSuiteReport,
)


def test_deflated_sharpe_ratio_computation():
    """Verify Probabilistic and Deflated Sharpe Ratio calculation."""
    # Generate 500 returns with positive mean and realistic fat tails
    rng = np.random.default_rng(42)
    returns = rng.normal(loc=0.001, scale=0.015, size=500)
    
    psr, dsr, exp_max_sr, stats = calculate_psr_and_dsr(
        returns=returns,
        annualization_factor=252.0,
        num_trials=20,
        benchmark_sr=0.0,
    )

    assert 0.0 <= psr <= 1.0
    assert 0.0 <= dsr <= 1.0
    assert exp_max_sr > 0.0
    assert "skewness" in stats
    assert "kurtosis" in stats
    assert "annualized_sharpe" in stats
    assert stats["annualized_sharpe"] > 0.0


def test_cpcv_with_purging_and_embargo():
    """Verify Combinatorial Purged Cross-Validation correctly purges and embargos overlapping serial bars."""
    dates = pd.date_range("2026-01-01", periods=1000, freq="1h")
    df = pd.DataFrame({"close": np.linspace(100, 200, 1000)}, index=dates)

    folds = run_cpcv_split(
        df=df,
        n_splits=6,
        n_test_splits=2,
        holding_period_bars=24,
        embargo_bars=48,
    )

    # 6 choose 2 = 15 combinatorial paths
    assert len(folds) == 15

    for fold in folds:
        train_idx = fold["train_indices"]
        test_idx = fold["test_indices"]
        # Intersection between train and test must be strictly empty (zero lookahead)
        assert len(set(train_idx).intersection(set(test_idx))) == 0
        assert len(train_idx) > 0
        assert len(test_idx) > 0


def test_slippage_multiplier_stress():
    """Verify execution friction stress test scales fees and detects break-even multiplier."""
    trades = [
        {"pnl": 100.0, "fees": 2.0, "units": 1.0, "entry_price": 1000.0, "exit_price": 1100.0},
        {"pnl": -50.0, "fees": 2.0, "units": 1.0, "entry_price": 1000.0, "exit_price": 950.0},
        {"pnl": 80.0, "fees": 2.0, "units": 1.0, "entry_price": 1000.0, "exit_price": 1080.0},
        {"pnl": 60.0, "fees": 2.0, "units": 1.0, "entry_price": 1000.0, "exit_price": 1060.0},
    ]

    stress_res = run_slippage_multiplier_stress(
        trades=trades,
        initial_capital=10000.0,
        multipliers=[1.0, 2.0, 3.0, 5.0],
    )

    assert len(stress_res["tiers"]) == 4
    # Higher friction must reduce net return
    ret_1x = stress_res["tiers"][0]["net_return_pct"]
    ret_5x = stress_res["tiers"][3]["net_return_pct"]
    assert ret_1x > ret_5x
    assert stress_res["break_even_multiplier"] > 0.0


def test_underwater_and_streak_metrics():
    """Verify underwater duration, recovery time, and consecutive loss/win streaks."""
    equity_curve = [
        10000.0, 10200.0, 10500.0,  # New ATH (10500)
        10400.0, 10300.0, 10100.0, 10250.0,  # 4 bars underwater
        10600.0,  # New ATH (recovered)
        10500.0, 10700.0  # Dip then new ATH
    ]
    trade_pnls = [200.0, 300.0, -100.0, -100.0, -200.0, 150.0, 350.0, -100.0, 200.0]

    metrics = calculate_underwater_and_streak_metrics(
        equity_curve=equity_curve,
        trade_pnls=trade_pnls,
        hours_per_bar=1.0,
    )

    assert metrics["max_underwater_hours"] == 4.0
    assert metrics["max_consecutive_losses"] == 3  # (-100, -100, -200)
    assert metrics["max_consecutive_wins"] == 2
    assert metrics["ulcer_index"] > 0.0
    assert metrics["calmar_ratio"] > 0.0


def test_crisis_stress_replay():
    """Verify bot behavior under simulated flash crash / crisis shock."""
    dates = pd.date_range("2026-01-01", periods=120, freq="1h")
    # Base asset price steady around 100
    prices = [100.0] * 120
    # Inject 2020/FTX-style crisis flash crash at bar 60: -35% drop in 3 bars
    prices[60] = 85.0
    prices[61] = 72.0
    prices[62] = 65.0
    # Gradual slow recovery
    for i in range(63, 120):
        prices[i] = 65.0 + (i - 62) * 0.3

    df_crisis = pd.DataFrame({
        "open": prices,
        "high": [p * 1.01 for p in prices],
        "low": [p * 0.99 for p in prices],
        "close": prices,
        "volume": [5000.0] * 120,
    }, index=dates)

    report = run_crisis_stress_replay(
        df=df_crisis,
        crisis_name="Flash Crash Shock (-35%)",
        initial_capital=10000.0,
        stop_loss_atr_mult=2.25,
    )

    assert report["crisis_name"] == "Flash Crash Shock (-35%)"
    # Bot stop-loss and cash defense should cap portfolio loss strictly less than asset drop (-35%)
    assert report["portfolio_max_dd_pct"] < 35.0
    assert report["circuit_breaker_triggered"] is True
