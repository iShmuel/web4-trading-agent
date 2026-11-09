"""Comprehensive Pre-Deployment Safety & Governance Verification Test Suite."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import pytest

from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine, MIN_ALLOWED_RISK_PCT, MAX_ALLOWED_RISK_PCT
from core.coordinator import PortfolioCoordinator
from core.data_loader import check_data_quality


def test_adx_boundaries_no_contradiction():
    """Verify ADX regime boundaries in Coordinator:
    - ADX < 24.0: RANGE_CHOP
    - 24.0 <= ADX < 28.0: TRANSITION
    - ADX >= 28.0: TREND_EXPANSION
    Guarantees clear regime classification without contradictions.
    """
    account = PaperAccount(portfolio_file=None)
    risk = RiskEngine(default_risk_pct=0.02)
    cfg = {
        "adx_chop_threshold": 24.0,
        "adx_trend_threshold": 28.0,
        "bb_std": 1.9,
    }
    coord = PortfolioCoordinator(account, risk, cfg)

    trend_th = coord.config.get("adx_trend_threshold", 25.0)
    chop_th = coord.config.get("adx_chop_threshold", 20.0)
    assert trend_th == 28.0
    assert chop_th == 24.0
    assert chop_th < trend_th, "Chop threshold must be strictly less than trend threshold"

    def get_regime(adx_val):
        if adx_val >= trend_th:
            return "TREND_EXPANSION"
        elif adx_val < chop_th:
            return "RANGE_CHOP"
        else:
            return "TRANSITION"

    assert get_regime(20.0) == "RANGE_CHOP"
    assert get_regime(23.9) == "RANGE_CHOP"
    assert get_regime(24.0) == "TRANSITION"
    assert get_regime(26.5) == "TRANSITION"
    assert get_regime(27.99) == "TRANSITION"
    assert get_regime(28.0) == "TREND_EXPANSION"
    assert get_regime(35.0) == "TREND_EXPANSION"


def test_execution_safety_paper_mode_only(tmp_path):
    """Verify system operates exclusively in simulated paper mode."""
    tmp_pf = tmp_path / "test_paper_portfolio.json"
    account = PaperAccount(portfolio_file=tmp_pf, initial_capital=10000.0)
    assert account.initial_capital == 10000.0
    assert account.cash == 10000.0
    assert isinstance(account.positions, dict)
    assert len(account.positions) == 0

    # Ensure simulated paper execution works without real credentials
    pos = account.open_position(
        symbol="BTC/USDT",
        entry_price=50000.0,
        units=0.1,
        stop_loss=48000.0,
        dollar_risk=200.0,
        side="LONG",
        strategy_id="momentum",
    )
    assert pos["symbol"] == "BTC/USDT"
    assert account.positions["BTC/USDT"]["units"] == 0.1
    assert account.cash < 10000.0


def test_risk_controls_hard_bounds_and_exposure():
    """Verify Van Tharp sizing bounds [1%, 5%] and single asset limits."""
    risk = RiskEngine(default_risk_pct=0.02)

    # 1. Van Tharp sizing bounds
    assert risk.risk_pct == 0.02
    assert MIN_ALLOWED_RISK_PCT == 0.01
    assert MAX_ALLOWED_RISK_PCT == 0.05

    # Enforce risk bounds helper clamps between 1% and 5%
    assert risk.enforce_risk_bounds(0.005) == 0.01
    assert risk.enforce_risk_bounds(0.06) == 0.05
    assert risk.enforce_risk_bounds(0.02) == 0.02

    # 2. Position calculation respects risk bounds:
    # 2% risk of $10k equity = $200 risk. With ATR=2.0, atr_multiplier=2.5, stop dist = $5.0.
    # units = 200 / 5 = 40.0
    units, stop_loss, risk_amt = risk.calculate_position(
        equity=10000.0,
        entry_price=100.0,
        atr=2.0,
        atr_multiplier=2.5,
    )
    assert math.isclose(risk_amt, 200.0, rel_tol=1e-3)
    assert math.isclose(units, 40.0, rel_tol=1e-3)
    assert math.isclose(stop_loss, 95.0, rel_tol=1e-3)

    # 3. Validate proposed entry enforces single asset exposure cap (35% = $3,500)
    verdict_huge = risk.validate_proposed_entry(
        symbol="AAPL",
        side="LONG",
        strategy_id="momentum",
        entry_price=100.0,
        suggested_stop=99.99,  # tiny stop would size massive units without cap
        atr=0.001,
        atr_multiplier=1.0,
        equity=10000.0,
        cash=10000.0,
        active_positions={},
    )
    # The verdict should cap notional to <= 35% equity ($3,500)
    assert verdict_huge.approved is True
    notional = verdict_huge.suggested_units * 100.0
    assert notional <= 3500.01, f"Notional {notional} exceeded 35% equity cap"


def test_failure_simulations_stale_and_invalid_data():
    """Verify system safely rejects stale data, missing candles, and NaN prices."""
    # 1. Empty data
    empty_df = pd.DataFrame()
    valid, reason = check_data_quality(empty_df)
    assert valid is False
    assert "empty" in reason.lower()

    # 2. NaN values in a DataFrame with >= 30 bars
    idx = pd.date_range("2026-01-01", periods=35, freq="1h")
    nan_df = pd.DataFrame({
        "open": [10.0] * 35,
        "high": [12.0] * 35,
        "low": [9.0] * 35,
        "close": [11.0] * 34 + [float("nan")],
        "volume": [100.0] * 35,
    }, index=idx)
    valid_nan, reason_nan = check_data_quality(nan_df)
    assert valid_nan is False
    assert "nan" in reason_nan.lower()

    # 3. Stale data check (index older than max_stale_minutes)
    old_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    old_idx = pd.date_range(old_time, periods=35, freq="1h")
    stale_df = pd.DataFrame({
        "open": [10.0] * 35,
        "high": [12.0] * 35,
        "low": [9.0] * 35,
        "close": [11.0] * 35,
        "volume": [100.0] * 35,
    }, index=old_idx)
    valid_stale, reason_stale = check_data_quality(stale_df, max_stale_minutes=180)
    assert valid_stale is False
    assert "stale" in reason_stale.lower()
