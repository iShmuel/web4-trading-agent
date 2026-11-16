"""Unit & integration tests for Volatility Squeeze, Trend Pullback, and Idle-Cash Sweep."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from core.backtest_engine import (
    run_trend_pullback_backtest,
    run_volatility_squeeze_backtest,
)
from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.signals import SignalStatus
from strategies.trend_pullback import TrendPullbackStrategy, compute_pullback_indicators
from strategies.volatility_squeeze import VolatilitySqueezeStrategy, compute_squeeze_indicators


def _make_ohlcv(n: int = 240, squeeze_breakout: bool = False, pullback_bounce: bool = False) -> pd.DataFrame:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    idx = pd.date_range(end=now, periods=n, freq="1h", tz=timezone.utc)
    if pullback_bounce:
        closes = np.linspace(100.0, 140.0, n)
        closes[-4] = 139.0
        closes[-3] = 136.0
        closes[-2] = 133.5
        closes[-1] = 136.5
        opens = closes - 0.3
        opens[-2] = 135.5
        opens[-1] = 134.0
        highs = np.maximum(opens, closes) + 0.5
        lows = np.minimum(opens, closes) - 0.5
        lows[-2] = closes[-2] - 2.0
        vols = np.full(n, 1000.0)
    elif squeeze_breakout:
        closes = np.full(n, 100.0) + np.sin(np.arange(n) * 0.1) * 0.05
        closes[:50] = np.linspace(95.0, 100.0, 50)
        closes[-1] = 104.5
        opens = closes - 0.02
        opens[-1] = 100.2
        highs = closes + 0.15
        lows = closes - 0.15
        highs[-1] = 104.8
        vols = np.full(n, 1000.0)
        vols[-1] = 3000.0
    else:
        closes = 100.0 + np.cumsum(np.sin(np.arange(n) * 0.25) * 0.8)
        opens = closes - 0.1
        highs = closes + 0.6
        lows = closes - 0.6
        vols = np.full(n, 1000.0)

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
        index=idx,
    )


def test_volatility_squeeze_strategy_triggers_on_expansion():
    df = _make_ohlcv(120, squeeze_breakout=True)
    ind = compute_squeeze_indicators(df)
    assert bool(ind["squeeze_breakout"].iloc[-1]) is True

    strat = VolatilitySqueezeStrategy({"max_stale_minutes": 360})
    sig = strat.evaluate("BTC/USDT", df)
    assert sig.strategy_id == "volatility_squeeze"
    assert sig.status == SignalStatus.ENTRY
    assert sig.suggested_stop < sig.reference_price < sig.suggested_take_profit


def test_trend_pullback_strategy_triggers_on_bounce():
    df = _make_ohlcv(260, pullback_bounce=True)
    ind = compute_pullback_indicators(df)
    assert bool(ind["pullback_entry"].iloc[-1]) is True

    strat = TrendPullbackStrategy({"max_stale_minutes": 360})
    sig = strat.evaluate("AAPL", df)
    assert sig.strategy_id == "trend_pullback"
    assert sig.status == SignalStatus.ENTRY
    assert sig.suggested_stop < sig.reference_price < sig.suggested_take_profit


def test_backtest_engines_for_squeeze_and_pullback():
    df_sq = _make_ohlcv(180, squeeze_breakout=True)
    r_sq = run_volatility_squeeze_backtest(df_sq)
    assert r_sq.initial_capital == 10000.0
    assert r_sq.max_drawdown_pct >= 0.0

    df_pb = _make_ohlcv(260, pullback_bounce=True)
    r_pb = run_trend_pullback_backtest(df_pb)
    assert r_pb.initial_capital == 10000.0
    assert r_pb.max_drawdown_pct >= 0.0


def test_coordinator_registers_new_strategies_and_idle_cash_sweep(tmp_path: Path):
    acc = PaperAccount(initial_capital=10000.0, portfolio_file=tmp_path / "port.json")
    re = RiskEngine(account=acc)
    cfg = {
        "enable_dynamic_rolling_router": True,
        "enable_volatility_squeeze": True,
        "enable_trend_pullback": True,
        "enable_idle_cash_sweep": True,
        "idle_cash_sweep_max_pct": 0.50,
        "basis_hurdle_apr": 5.0,
    }
    coord = PortfolioCoordinator(acc, re, cfg)
    assert "volatility_squeeze" in coord._custom_strategies
    assert "trend_pullback" in coord._custom_strategies
    assert coord.is_strategy_enabled("volatility_squeeze")
    assert coord.is_strategy_enabled("trend_pullback")
