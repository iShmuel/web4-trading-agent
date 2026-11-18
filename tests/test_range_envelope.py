"""Unit and integration tests for RangeEnvelopeStrategy (9th strategy engine)."""

import numpy as np
import pandas as pd

from core.backtest_engine import run_range_envelope_backtest
from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.signals import SignalDirection, SignalStatus
from strategies.range_envelope import (
    RangeEnvelopeStrategy,
    compute_range_envelope_indicators,
)


def _make_range_df(n: int = 120, amp: float = 8.0, base: float = 100.0) -> pd.DataFrame:
    """Create a synthetic sideways range-bound OHLCV series oscillating between base-amp and base+amp."""
    idx = pd.date_range("2026-10-01", periods=n, freq="1h", tz="UTC")
    t = np.arange(n, dtype=float)
    wave = base + amp * np.sin(2.0 * np.pi * t / 16.0)
    close = wave.copy()
    open_p = wave - 0.3 * np.cos(2.0 * np.pi * t / 16.0)
    high = np.maximum(open_p, close) + 0.1
    low = np.minimum(open_p, close) - 0.1
    vol = np.full(n, 5000.0)
    return pd.DataFrame(
        {"open": open_p, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def test_compute_range_envelope_indicators_causal():
    df = _make_range_df(90)
    ind = compute_range_envelope_indicators(df, range_window=36, low_quantile=0.10, high_quantile=0.90)
    assert "avg_down" in ind.columns
    assert "avg_top" in ind.columns
    valid = ind.dropna(subset=["avg_down", "avg_top"])
    assert len(valid) > 40
    assert (valid["avg_top"] > valid["avg_down"]).all()


def test_range_envelope_rejects_narrow_range_below_net_dollar_floor():
    # Very narrow range (amp=0.05 on $100 price -> 0.1% total range, cannot cover fees + $10 net)
    df = _make_range_df(100, amp=0.05, base=100.0)
    strat = RangeEnvelopeStrategy({"range_window": 36, "range_min_net_profit_usd": 25.0, "max_stale_minutes": 0})
    sig = strat.evaluate("TEST/USDT", df)
    assert sig.status == SignalStatus.NO_TRADE


def test_range_envelope_entry_and_exit_lifecycle():
    df = _make_range_df(120, amp=6.0, base=100.0)
    # Force final bar into oversold dip below avg_down in low-ADX range
    df.iloc[-1, df.columns.get_loc("close")] = 91.5
    df.iloc[-1, df.columns.get_loc("low")] = 91.0
    strat = RangeEnvelopeStrategy({
        "range_window": 36,
        "range_rsi_oversold": 48.0,
        "range_adx_max": 60.0,
        "range_min_net_profit_usd": 10.0,
        "max_stale_minutes": 0,
    })
    sig = strat.evaluate("TEST/USDT", df)
    assert sig.status == SignalStatus.ENTRY
    assert sig.direction == SignalDirection.LONG
    assert sig.suggested_take_profit > sig.reference_price
    assert sig.take_profit_ratio == 0.5

    # Simulate partial scale-out when price reaches midpoint
    pos = {
        "side": "LONG",
        "entry_price": 91.5,
        "trailing_stop": sig.suggested_stop,
        "units": 25.0,
        "tp_hit": False,
    }
    df_mid = df.copy()
    df_mid.iloc[-1, df_mid.columns.get_loc("high")] = 100.0
    df_mid.iloc[-1, df_mid.columns.get_loc("close")] = 99.8
    sig_part = strat.evaluate("TEST/USDT", df_mid, existing_position=pos)
    assert sig_part.status == SignalStatus.PARTIAL_EXIT


def test_run_range_envelope_backtest_and_coordinator_registration(tmp_path):
    df = _make_range_df(220, amp=6.0, base=100.0)
    res = run_range_envelope_backtest(
        df,
        range_window=36,
        rsi_oversold=45.0,
        rsi_overbought=55.0,
        adx_max=50.0,
        min_net_profit_usd=10.0,
    )
    assert res.total_trades >= 1
    assert len(res.equity_curve) > 10

    coord = PortfolioCoordinator(None, None, {"enable_range_envelope": True})
    assert coord.is_strategy_enabled("range_envelope")
    assert "range_envelope" in coord.get_registered_strategies()
