"""Tests for Dynamic Multi-Asset Pairs Trading and Live Basis Yield Accrual Integration."""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.coordinator import PortfolioCoordinator
from strategies.stat_arb_pairs import StatArbPairsStrategy, PairTradeSignal


@pytest.fixture
def test_setup(tmp_path):
    pfile = tmp_path / "test_portfolio.json"
    account = PaperAccount(10000.0, portfolio_file=pfile)
    risk_engine = RiskEngine(default_risk_pct=0.02, max_open_positions=5)
    config = {
        "account_risk_pct": 0.02,
        "max_concurrent_positions": 5,
        "enable_pairs_trading": True,
        "enable_basis_yield": True,
        "pairs_universe": [("GLD", "SLV"), ("NVDA", "AMD"), ("BTC/USDT", "ETH/USDT")],
        "min_pair_correlation": 0.70,
        "max_pair_half_life_hours": 72.0,
        "stat_arb_entry_z": 2.0,
        "stat_arb_exit_z": 0.5,
        "stat_arb_stop_z": 3.5,
        "basis_hurdle_apr": 8.0,
        "max_basis_allocation_pct": 0.30,
    }
    coordinator = PortfolioCoordinator(account, risk_engine, config)
    return account, risk_engine, coordinator, config


def test_dynamic_pairs_scanner_identifies_cointegrated_pairs():
    """Verify that dynamic pairs scanner filters out trending pairs and detects stationary mean-reverting pairs."""
    dates = pd.date_range("2026-01-01", periods=100, freq="1h", tz="UTC")
    
    # Pair 1: Cointegrated (GLD vs SLV) with stationary mean-reverting spread
    base_slv = 30.0 + np.sin(np.linspace(0, 15, 100)) * 2.0
    base_gld = 2.5 * base_slv + 10.0 + np.cos(np.linspace(0, 40, 100)) * 1.5
    # Add temporary spread divergence at the end within entry band (2.0 <= |Z| < 3.5)
    base_gld[-1] -= 2.5  # Underpriced spread -> Long GLD, Short SLV
    
    # Pair 2: Non-cointegrated / trending (Diverging secular drift)
    trend_a = np.linspace(100, 200, 100)
    trend_b = np.linspace(100, 50, 100)
    
    market_data = {
        "GLD": pd.DataFrame({"close": base_gld, "high": base_gld + 0.5, "low": base_gld - 0.5, "open": base_gld, "volume": 1000}, index=dates),
        "SLV": pd.DataFrame({"close": base_slv, "high": base_slv + 0.5, "low": base_slv - 0.5, "open": base_slv, "volume": 1000}, index=dates),
        "TREND_A": pd.DataFrame({"close": trend_a, "high": trend_a + 1, "low": trend_a - 1, "open": trend_a, "volume": 1000}, index=dates),
        "TREND_B": pd.DataFrame({"close": trend_b, "high": trend_b + 1, "low": trend_b - 1, "open": trend_b, "volume": 1000}, index=dates),
    }

    strategy = StatArbPairsStrategy({
        "stat_arb_lookback": 48,
        "min_pair_correlation": 0.70,
        "max_pair_half_life_hours": 72.0,
        "stat_arb_entry_z": 2.0,
    })

    signals = strategy.scan_dynamic_pairs(
        market_data,
        candidate_universe=[("GLD", "SLV"), ("TREND_A", "TREND_B")],
    )

    # GLD/SLV should be accepted and signal entry
    gld_slv_sig = next((s for s in signals if s.pair_id == "GLD:SLV"), None)
    assert gld_slv_sig is not None
    assert gld_slv_sig.action in ("LONG_A_SHORT_B", "SHORT_A_LONG_B")
    assert gld_slv_sig.is_stationary is True
    assert gld_slv_sig.correlation >= 0.70

    # Diverging trend pair should be rejected / marked non-stationary
    trend_sig = next((s for s in signals if s.pair_id == "TREND_A:TREND_B"), None)
    assert trend_sig is None or trend_sig.action == "NO_TRADE"


def test_coordinator_dynamic_pair_execution_and_dual_leg_exit(test_setup):
    """Verify coordinator opens and exits dual legs in paper account for dynamic pairs."""
    account, risk_engine, coordinator, config = test_setup
    dates = pd.date_range("2026-01-01", periods=100, freq="1h", tz="UTC")

    base_slv = 30.0 + np.sin(np.linspace(0, 15, 100)) * 2.0
    base_gld = 2.5 * base_slv + 10.0 + np.cos(np.linspace(0, 40, 100)) * 1.5
    # Create underpriced spread inside valid entry band (2.0 <= |Z| < 3.5)
    base_gld[-1] -= 2.5

    df_gld = pd.DataFrame({"close": base_gld, "high": base_gld + 0.5, "low": base_gld - 0.5, "open": base_gld, "volume": 1000}, index=dates)
    df_slv = pd.DataFrame({"close": base_slv, "high": base_slv + 0.5, "low": base_slv - 0.5, "open": base_slv, "volume": 1000}, index=dates)

    market_data = {"GLD": df_gld, "SLV": df_slv}

    # Step 1: Run cycle - should execute dual legs
    report = coordinator.run_cycle(market_data)

    # Assert both legs are open in paper account
    assert account.has_position("GLD")
    assert account.has_position("SLV")
    pos_gld = account.get_position("GLD")
    pos_slv = account.get_position("SLV")

    assert pos_gld["side"] == "LONG"
    assert pos_slv["side"] == "SHORT"
    assert pos_gld.get("pair_id") == "GLD:SLV"
    assert pos_slv.get("pair_id") == "GLD:SLV"

    # Step 2: Mean-revert the spread back to normal (target hit)
    cov = np.cov(base_gld[-48:], base_slv[-48:])[0, 1]
    var_b = np.var(base_slv[-48:])
    beta = cov / var_b if var_b > 0 else 1.0
    spread = pd.Series(base_gld[-48:]) - (beta * pd.Series(base_slv[-48:]))
    base_gld[-1] = (beta * base_slv[-1]) + float(spread.mean())
    df_gld = pd.DataFrame({"close": base_gld, "high": base_gld + 0.5, "low": base_gld - 0.5, "open": base_gld, "volume": 1000}, index=dates)
    market_data = {"GLD": df_gld, "SLV": df_slv}

    report2 = coordinator.run_cycle(market_data)

    # Both legs should now be closed simultaneously
    assert not account.has_position("GLD")
    assert not account.has_position("SLV")
    assert len(account.state.get("closed_trades", [])) >= 2


def test_coordinator_basis_yield_accrual_cycle(test_setup, monkeypatch):
    """Verify coordinator periodically evaluates and accrues basis yield in live paper loop."""
    account, risk_engine, coordinator, config = test_setup

    initial_cash = account.cash

    # Mock fetch_funding_rate to return +0.02% per 8h for BTC
    from core import basis_yield
    monkeypatch.setattr(basis_yield, "fetch_funding_rate", lambda sym: 0.0002 if "BTC" in sym else 0.0)

    dates = pd.date_range("2026-01-01", periods=50, freq="1h", tz="UTC")
    df_btc = pd.DataFrame({"close": [60000.0] * 50, "high": [60100.0] * 50, "low": [59900.0] * 50, "open": [60000.0] * 50, "volume": [100.0] * 50}, index=dates)
    market_data = {"BTC/USDT": df_btc}

    # Trigger cycle with basis yield enabled
    coordinator.last_basis_accrual_time = None  # Force initial trigger
    report = coordinator.run_cycle(market_data)

    # Cash should have increased from basis yield payout
    assert account.cash > initial_cash
    assert coordinator.basis_yield_engine.total_accrued_yield > 0.0
    closed = account.state.get("closed_trades", [])
    assert any(t.get("strategy_id") == "basis_yield" for t in closed)


def test_volatility_scaled_pair_leg_sizing(test_setup):
    """Volatile pairs should receive wider stop_pct and tighter leg notional cap (<=7% equity)."""
    _, _, coordinator, _ = test_setup
    dates = pd.date_range("2026-01-01", periods=30, freq="1h", tz="UTC")
    low_vol_df = pd.DataFrame({
        "close": np.full(30, 100.0),
        "high": np.full(30, 100.25),
        "low": np.full(30, 99.75),
    }, index=dates)
    high_vol_df = pd.DataFrame({
        "close": np.full(30, 0.12),
        "high": np.full(30, 0.123),
        "low": np.full(30, 0.118),
    }, index=dates)

    stop_low, leg_low, _ = coordinator._compute_pair_leg_sizing(low_vol_df, low_vol_df, 10000.0, 100.0)
    assert stop_low == pytest.approx(0.05)
    assert leg_low == pytest.approx(1500.0)  # 15% cap on calm assets

    stop_high, leg_high, total_high = coordinator._compute_pair_leg_sizing(high_vol_df, low_vol_df, 10000.0, 100.0)
    assert stop_high > 0.09  # 2.5 * ~4.17% ATR
    assert leg_high <= 700.0 + 1e-6  # 7% equity cap on volatile >=3% ATR assets
    assert total_high <= 1400.0 + 1e-6


def test_pair_stop_persistence_and_two_strike_blacklist(tmp_path):
    """Pair stop counts and cooldowns persist to PaperAccount and blacklist after 2 strikes."""
    pfile = tmp_path / "persist_pairs.json"
    acct1 = PaperAccount(10000.0, portfolio_file=pfile)
    re1 = RiskEngine(default_risk_pct=0.02, max_open_positions=5)
    cfg = {"enable_pairs_trading": True, "pair_stop_cooldown_hours": 12.0}
    coord1 = PortfolioCoordinator(acct1, re1, cfg)

    now = datetime.now(timezone.utc)
    coord1._record_pair_stop("INIT/USDT:GRAM/USDT", now)
    assert coord1.pair_stop_counts["INIT/USDT:GRAM/USDT"] == 1
    assert "INIT/USDT:GRAM/USDT" not in coord1.pairs_strategy.blacklisted_pairs

    # Rehydrate from disk and trigger 2nd strike
    acct2 = PaperAccount(10000.0, portfolio_file=pfile)
    re2 = RiskEngine(default_risk_pct=0.02, max_open_positions=5)
    coord2 = PortfolioCoordinator(acct2, re2, cfg)
    assert coord2.pair_stop_counts.get("INIT/USDT:GRAM/USDT") == 1
    assert "INIT/USDT:GRAM/USDT" in coord2.pair_cooldown_until

    coord2._record_pair_stop("INIT/USDT:GRAM/USDT", now + timedelta(hours=13))
    assert coord2.pair_stop_counts["INIT/USDT:GRAM/USDT"] == 2
    assert "INIT/USDT:GRAM/USDT" in coord2.pairs_strategy.blacklisted_pairs

    # 3rd restart must keep it blacklisted automatically
    acct3 = PaperAccount(10000.0, portfolio_file=pfile)
    coord3 = PortfolioCoordinator(acct3, RiskEngine(default_risk_pct=0.02, max_open_positions=5), cfg)
    assert "INIT/USDT:GRAM/USDT" in coord3.pairs_strategy.blacklisted_pairs


def test_discover_cointegrated_pairs_filters_illiquid_microcaps():
    """discover_cointegrated_pairs skips sub-floor price or low-dollar-volume assets when configured."""
    from strategies.stat_arb_pairs import discover_cointegrated_pairs

    dates = pd.date_range("2026-01-01", periods=100, freq="1h", tz="UTC")
    base = 100.0 + np.sin(np.linspace(0, 15, 100)) * 5.0
    micro = 0.02 + np.sin(np.linspace(0, 15, 100)) * 0.001

    market_data = {
        "BTC/USDT": pd.DataFrame({"close": base * 500, "high": base * 501, "low": base * 499, "volume": 50.0}, index=dates),
        "BNB/USDT": pd.DataFrame({"close": base * 5, "high": base * 5.05, "low": base * 4.95, "volume": 500.0}, index=dates),
        "MICRO/USDT": pd.DataFrame({"close": micro, "high": micro * 1.01, "low": micro * 0.99, "volume": 1000.0}, index=dates),
    }

    selected = discover_cointegrated_pairs(
        ["BTC/USDT", "BNB/USDT", "MICRO/USDT"],
        config={"min_pair_price": 0.10, "min_pair_hourly_dollar_vol": 25000.0, "min_pair_correlation": 0.70},
        market_data=market_data,
    )
    flat_syms = {s for pair in selected for s in pair}
    assert "MICRO/USDT" not in flat_syms
