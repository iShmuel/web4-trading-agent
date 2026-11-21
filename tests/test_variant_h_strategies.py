"""Tests for Variant H Strategies: Williams %R Reversal (12th) & Bear Chandelier Short (13th)."""

import numpy as np
import pandas as pd

from agent.governor import PARAMETER_BOUNDS, audit_invariants
from core.backtest_engine import (
    run_bear_chandelier_short_backtest,
    run_williams_r_reversal_backtest,
)
from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.signals import SignalDirection, SignalStatus
from strategies.bear_chandelier_short import (
    BearChandelierShortStrategy,
    compute_bear_chandelier_indicators,
)
from strategies.williams_r_reversal import (
    WilliamsRReversalStrategy,
    compute_williams_r_indicators,
)


def _make_sideways_ohlcv(bars: int = 160) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=bars, freq="1h", tz="UTC")
    t = np.linspace(0, 10 * np.pi, bars)
    close = 100.0 + 4.0 * np.sin(t)
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": np.full(bars, 20000.0),
        },
        index=idx,
    )


def _make_bear_relief_rally_ohlcv(bars: int = 240) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=bars, freq="1h", tz="UTC")
    close = np.full(bars, 100.0)
    close[:150] = np.linspace(260.0, 100.0, 150)
    # Flat base at 100 (bars 150..184), then sharp relief rally to 118 and red rejection at bar 190
    close[185:191] = [103.0, 106.0, 110.0, 114.0, 118.0, 116.0]
    close[191:] = np.linspace(112.0, 82.0, bars - 191)
    return pd.DataFrame(
        {
            "open": close * 1.001,
            "high": close * 1.006,
            "low": close * 0.994,
            "close": close,
            "volume": np.full(bars, 30000.0),
        },
        index=idx,
    )


def test_williams_r_reversal_backtest_and_signals():
    df = _make_sideways_ohlcv(180)
    ind = compute_williams_r_indicators(df)
    assert "williams_r" in ind.columns and "adx" in ind.columns

    res = run_williams_r_reversal_backtest(df, initial_capital=10000.0, account_risk_pct=0.02, adx_max=45.0)
    assert res.total_trades >= 1
    assert res.final_equity > 0

    strat = WilliamsRReversalStrategy({"max_stale_minutes": 9999999, "williams_r_adx_max": 45.0})
    wr = ind["williams_r"].values
    long_idx = [i for i in range(45, len(wr)) if wr[i - 1] < -85.0 and wr[i] >= -80.0]
    assert long_idx
    sig = strat.evaluate("AAPL", df.iloc[: long_idx[0] + 1].copy(), existing_position=None)
    assert sig.status == SignalStatus.ENTRY
    assert sig.direction == SignalDirection.LONG
    assert sig.atr > 0


def test_bear_chandelier_short_backtest_and_signals():
    df = _make_bear_relief_rally_ohlcv(240)
    ind = compute_bear_chandelier_indicators(df)
    assert "zscore" in ind.columns and "chand_short_stop" in ind.columns

    res = run_bear_chandelier_short_backtest(df, initial_capital=10000.0, account_risk_pct=0.02)
    assert res.total_trades >= 1
    assert res.final_equity > 0

    strat = BearChandelierShortStrategy({"max_stale_minutes": 9999999})
    sig = strat.evaluate("AAPL", df.iloc[:191].copy(), existing_position=None)
    assert sig.status == SignalStatus.ENTRY
    assert sig.direction == SignalDirection.SHORT
    assert sig.atr > 0
    assert sig.suggested_stop > sig.reference_price


def test_variant_h_wired_into_coordinator_and_governor(tmp_path):
    acct = PaperAccount(initial_capital=10000.0, portfolio_file=tmp_path / "port.json")
    coord = PortfolioCoordinator(
        account=acct,
        risk_engine=RiskEngine(default_risk_pct=0.02),
        config={"enable_williams_r_reversal": True, "enable_bear_chandelier_short": True},
    )
    assert coord.is_strategy_enabled("williams_r_reversal")
    assert coord.is_strategy_enabled("bear_chandelier_short")
    for k in ("williams_r_period", "williams_r_adx_max", "bear_chand_z_entry", "bear_chand_mult"):
        assert k in PARAMETER_BOUNDS
    ok, errs = audit_invariants({"account_risk_pct": 0.02, "williams_r_period": 14, "bear_chand_mult": 3.0})
    assert ok, errs
