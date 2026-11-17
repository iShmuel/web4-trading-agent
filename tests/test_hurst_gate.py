"""Unit tests for the Hurst Exponent Gate on Mean Reversion."""

import numpy as np
import pandas as pd

from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.strategy import compute_hurst_exponent


def _make_ohlcv(closes: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.005,
            "low": closes * 0.995,
            "close": closes,
            "volume": np.full(len(closes), 1000.0),
        },
        index=idx,
    )


def test_hurst_exponent_distinguishes_mean_reverting_vs_trending():
    rng = np.random.default_rng(42)
    # Mean-reverting OU-like oscillation
    mr = np.zeros(240)
    for i in range(1, len(mr)):
        mr[i] = -0.6 * mr[i - 1] + rng.normal(0, 0.5)
    h_mr = compute_hurst_exponent(pd.Series(100.0 + mr), max_lag=20)

    # Persistent trending series
    trend = np.cumsum(np.full(240, 0.5) + rng.normal(0, 0.1, 240)) + 100.0
    h_trend = compute_hurst_exponent(pd.Series(trend), max_lag=20)

    assert h_mr < 0.52
    assert h_trend >= 0.52


def test_coordinator_blocks_mean_reversion_when_hurst_gate_triggers(tmp_path):
    rng = np.random.default_rng(7)
    trend_closes = np.cumsum(np.full(240, 0.5) + rng.normal(0, 0.1, 240)) + 100.0
    df = _make_ohlcv(trend_closes)

    cfg = {
        "enable_momentum": False,
        "enable_mean_reversion": True,
        "enable_pairs_trading": False,
        "enable_basis_yield": False,
        "enable_dynamic_rolling_router": False,
        "enable_hurst_gate": True,
        "hurst_mr_max": 0.52,
        "enable_macro_event_radar": False,
        "enable_vix_regime_filter": False,
        "enable_sec_8k_gate": False,
        "enable_sec_fundamental_gate": False,
        "enable_nyse_session_gate": False,
        "enable_local_llm_preflight": False,
        "enable_wash_and_streak_guard": False,
    }
    acct = PaperAccount(initial_capital=10000.0, portfolio_file=tmp_path / "port.json")
    risk = RiskEngine(default_risk_pct=0.02)
    coord = PortfolioCoordinator(account=acct, risk_engine=risk, config=cfg)

    dec = coord.process_symbol("TEST_TREND", df, {"TEST_TREND": float(df["close"].iloc[-1])})
    assert dec.action == "REJECTED"
    assert dec.indicators.get("hurst", 0.0) >= 0.52


def test_agent11_and_governor_support_hurst_mr_max():
    from agent.governor import PARAMETER_BOUNDS, audit_invariants
    from agent.multi_strategy_optimizer import MEAN_REVERSION_GRID

    assert "hurst_mr_max" in PARAMETER_BOUNDS
    assert "hurst_mr_max" in MEAN_REVERSION_GRID

    ok, errs = audit_invariants({"account_risk_pct": 0.02, "hurst_mr_max": 0.52})
    assert ok, errs
    bad_ok, bad_errs = audit_invariants({"account_risk_pct": 0.02, "hurst_mr_max": 0.90})
    assert not bad_ok
    assert any("hurst_mr_max" in e for e in bad_errs)
