"""Unit & integration tests for VWAP Fair-Value Reversion strategy."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from core.backtest_engine import run_vwap_reversion_backtest
from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.signals import SignalStatus
from strategies.vwap_reversion import VwapReversionStrategy, compute_vwap_indicators


def _make_vwap_bounce_df(n: int = 120) -> pd.DataFrame:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    idx = pd.date_range(end=now, periods=n, freq="1h", tz=timezone.utc)
    closes = np.full(n, 100.0) + np.sin(np.arange(n) * 0.2) * 0.5
    closes[-10:-1] = np.linspace(99.5, 92.0, 9)
    closes[-1] = 93.2
    opens = closes + 0.2
    opens[-1] = 92.1
    highs = np.maximum(opens, closes) + 0.4
    lows = np.minimum(opens, closes) - 0.4
    vols = np.full(n, 1000.0)
    vols[-10:-1] = 2000.0
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}, index=idx)


def test_vwap_reversion_triggers_on_oversold_bounce():
    df = _make_vwap_bounce_df(120)
    ind = compute_vwap_indicators(df)
    assert bool(ind["vwap_entry"].iloc[-1]) is True

    strat = VwapReversionStrategy({"max_stale_minutes": 360})
    sig = strat.evaluate("AAPL", df)
    assert sig.strategy_id == "vwap_reversion"
    assert sig.status == SignalStatus.ENTRY
    assert sig.suggested_stop < sig.reference_price < sig.suggested_take_profit


def test_vwap_reversion_backtest_and_coordinator_registration(tmp_path: Path):
    df = _make_vwap_bounce_df(140)
    res = run_vwap_reversion_backtest(df)
    assert res.initial_capital == 10000.0
    assert res.max_drawdown_pct >= 0.0

    acc = PaperAccount(initial_capital=10000.0, portfolio_file=tmp_path / "port.json")
    re = RiskEngine(account=acc)
    coord = PortfolioCoordinator(acc, re, {"enable_vwap_reversion": True})
    assert "vwap_reversion" in coord._custom_strategies
    assert coord.is_strategy_enabled("vwap_reversion")
