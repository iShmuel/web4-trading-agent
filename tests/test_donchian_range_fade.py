"""Tests for the 11th built-in strategy: Donchian Range Fade + RSI(2)."""

import numpy as np
import pandas as pd

from agent.governor import PARAMETER_BOUNDS, audit_invariants
from core.backtest_engine import run_donchian_range_fade_backtest
from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.signals import SignalDirection, SignalStatus
from strategies.donchian_range_fade import (
    DonchianRangeFadeStrategy,
    compute_donchian_range_fade_indicators,
)


def _make_compressed_range_ohlcv(bars: int = 240) -> pd.DataFrame:
    """Wide volatile first half followed by a tighter oscillating range so donch_narrow triggers."""
    idx = pd.date_range("2025-01-01", periods=bars, freq="1h", tz="UTC")
    t1 = np.linspace(0, 6 * np.pi, bars // 2)
    t2 = np.linspace(0, 12 * np.pi, bars - bars // 2)
    c1 = 100.0 + 12.0 * np.sin(t1)
    c2 = 100.0 + 3.0 * np.sin(t2)
    close = np.concatenate([c1, c2])
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.004,
            "low": close * 0.996,
            "close": close,
            "volume": np.full(bars, 20000.0),
        },
        index=idx,
    )


def test_donchian_range_fade_indicators_and_backtest():
    df = _make_compressed_range_ohlcv(260)
    ind = compute_donchian_range_fade_indicators(df)
    assert "donch_high" in ind.columns
    assert "donch_low" in ind.columns
    assert "donch_mid" in ind.columns
    assert "rsi2" in ind.columns
    assert bool(ind["donch_narrow"].iloc[130:].any())

    res = run_donchian_range_fade_backtest(df, initial_capital=10000.0, account_risk_pct=0.02)
    assert res.total_trades >= 1
    assert res.final_equity > 0


def test_donchian_range_fade_entry_and_midline_exit():
    df = _make_compressed_range_ohlcv(240)
    # Inject a sharp 3-bar oversold dip at bar 180 to touch Donchian low with RSI(2) < 10
    df.iloc[178, df.columns.get_loc("close")] = 97.5
    df.iloc[179, df.columns.get_loc("close")] = 96.8
    df.iloc[180, df.columns.get_loc("close")] = 96.0
    df.iloc[180, df.columns.get_loc("low")] = 95.9
    ind = compute_donchian_range_fade_indicators(df)
    long_bars = [
        i for i in range(130, len(ind))
        if bool(ind["donch_narrow"].iloc[i])
        and float(ind["close"].iloc[i]) <= float(ind["donch_low"].iloc[i]) * 1.002
        and float(ind["rsi2"].iloc[i]) < 10.0
    ]
    assert long_bars

    strat = DonchianRangeFadeStrategy({"max_stale_minutes": 9999999})
    sig = strat.evaluate("AAPL", df.iloc[: long_bars[0] + 1].copy(), existing_position=None)
    assert sig.status == SignalStatus.ENTRY
    assert sig.direction == SignalDirection.LONG
    assert sig.suggested_stop < sig.reference_price

    # Find a bar where close >= donch_mid to verify midline EXIT
    mid_bars = [
        i for i in range(long_bars[0] + 1, len(ind))
        if float(ind["close"].iloc[i]) >= float(ind["donch_mid"].iloc[i])
    ]
    assert mid_bars
    sig_exit = strat.evaluate(
        "AAPL",
        df.iloc[: mid_bars[0] + 1].copy(),
        existing_position={"side": "LONG", "trailing_stop": 50.0},
    )
    assert sig_exit.status == SignalStatus.EXIT
    assert "midline" in sig_exit.exit_reason.lower()


def test_donchian_range_fade_wired_into_coordinator_and_governor(tmp_path):
    acct = PaperAccount(initial_capital=10000.0, portfolio_file=tmp_path / "port.json")
    coord = PortfolioCoordinator(
        account=acct,
        risk_engine=RiskEngine(default_risk_pct=0.02),
        config={"enable_donchian_range_fade": True},
    )
    assert coord.is_strategy_enabled("donchian_range_fade")
    assert "donchian_range_fade" in coord.get_registered_strategies()

    for key in ("donchian_fade_window", "donchian_fade_rsi_low", "donchian_fade_rsi_high", "donchian_fade_stop_atr_mult"):
        assert key in PARAMETER_BOUNDS

    ok, errs = audit_invariants({"account_risk_pct": 0.02, "donchian_fade_window": 20, "donchian_fade_rsi_low": 10.0})
    assert ok, errs
    bad_ok, _ = audit_invariants({"account_risk_pct": 0.02, "donchian_fade_window": 200})
    assert not bad_ok
