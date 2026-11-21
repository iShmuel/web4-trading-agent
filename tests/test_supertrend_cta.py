"""Tests for the 10th built-in strategy: ATR Supertrend CTA (strategies/supertrend_cta.py)."""

import numpy as np
import pandas as pd

from agent.governor import PARAMETER_BOUNDS, audit_invariants
from core.backtest_engine import run_supertrend_cta_backtest
from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.signals import SignalDirection, SignalStatus
from strategies.supertrend_cta import SupertrendCtaStrategy, compute_supertrend_indicators


def _make_wave_ohlcv(bars: int = 160) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=bars, freq="1h", tz="UTC")
    # Smooth sine + trend waves to trigger clean bullish & bearish Supertrend flips
    t = np.linspace(0, 4 * np.pi, bars)
    close = 100.0 + 18.0 * np.sin(t) + np.linspace(0, 10, bars)
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.008,
            "low": close * 0.992,
            "close": close,
            "volume": np.full(bars, 25000.0),
        },
        index=idx,
    )


def test_supertrend_indicators_and_backtest():
    df = _make_wave_ohlcv(200)
    ind = compute_supertrend_indicators(df, atr_period=10, mult=2.5)
    assert "supertrend_dir" in ind.columns
    assert "supertrend_line" in ind.columns
    assert set(ind["supertrend_dir"].unique()).issubset({-1, 1})

    res = run_supertrend_cta_backtest(df, initial_capital=10000.0, account_risk_pct=0.02, mult=2.5)
    assert res.total_trades >= 2
    assert res.final_equity > 0


def test_supertrend_strategy_entry_and_exit_signals():
    df = _make_wave_ohlcv(160)
    ind = compute_supertrend_indicators(df, atr_period=10, mult=2.5)
    dirs = ind["supertrend_dir"].values
    bull_flips = [i for i in range(45, len(dirs)) if dirs[i] == 1 and dirs[i - 1] == -1]
    bear_flips = [i for i in range(45, len(dirs)) if dirs[i] == -1 and dirs[i - 1] == 1]
    assert bull_flips and bear_flips

    strat = SupertrendCtaStrategy({"supertrend_atr_period": 10, "supertrend_equity_mult": 2.5, "max_stale_minutes": 9999999})
    # Slice ending on a bullish flip
    sub_bull = df.iloc[: bull_flips[0] + 1].copy()
    sig_long = strat.evaluate("AAPL", sub_bull, existing_position=None)
    assert sig_long.status == SignalStatus.ENTRY
    assert sig_long.direction == SignalDirection.LONG
    assert sig_long.suggested_stop < sig_long.reference_price

    # Slice ending on a bearish flip with an open LONG -> EXIT
    sub_bear = df.iloc[: bear_flips[0] + 1].copy()
    sig_exit = strat.evaluate("AAPL", sub_bear, existing_position={"side": "LONG", "trailing_stop": 50.0})
    assert sig_exit.status == SignalStatus.EXIT
    assert "BEARISH" in sig_exit.exit_reason


def test_supertrend_wired_into_coordinator_and_governor(tmp_path):
    acct = PaperAccount(initial_capital=10000.0, portfolio_file=tmp_path / "port.json")
    coord = PortfolioCoordinator(
        account=acct,
        risk_engine=RiskEngine(default_risk_pct=0.02),
        config={"enable_supertrend_cta": True},
    )
    assert coord.is_strategy_enabled("supertrend_cta")
    assert "supertrend_cta" in coord.get_registered_strategies()

    for key in ("supertrend_atr_period", "supertrend_crypto_mult", "supertrend_equity_mult", "supertrend_stop_atr_mult"):
        assert key in PARAMETER_BOUNDS

    ok, errs = audit_invariants({"account_risk_pct": 0.02, "supertrend_atr_period": 10, "supertrend_crypto_mult": 3.0})
    assert ok, errs
    bad_ok, _ = audit_invariants({"account_risk_pct": 0.02, "supertrend_crypto_mult": 25.0})
    assert not bad_ok
