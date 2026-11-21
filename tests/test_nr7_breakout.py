"""Unit tests for NR7 Breakout strategy, backtest, Coordinator, Agent 11, and Governor."""

import numpy as np
import pandas as pd

from agent.governor import PARAMETER_BOUNDS, audit_invariants
from agent.multi_strategy_optimizer import NR7_BREAKOUT_GRID, build_optimization_tasks
from core.backtest_engine import run_nr7_breakout_backtest
from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.signals import SignalStatus
from strategies.nr7_breakout import Nr7BreakoutStrategy


def _make_nr7_df() -> pd.DataFrame:
    end = pd.Timestamp.now("UTC").floor("1h") - pd.Timedelta(hours=1)
    idx = pd.date_range(end=end, periods=85, freq="1h", tz="UTC")
    closes = np.linspace(100.0, 110.0, 85)
    highs = closes + 1.2
    lows = closes - 1.2
    # Make bar -7 an ultra-narrow NR7 compression bar
    highs[-7] = closes[-7] + 0.05
    lows[-7] = closes[-7] - 0.05
    # Make bar -6 break above bar -7's high, then rally to take-profit
    closes[-6] = highs[-7] + 0.8
    highs[-6] = closes[-6] + 0.5
    lows[-6] = closes[-6] - 0.5
    for k in range(-5, 0):
        closes[k] = closes[-6] + (6 + k) * 2.5
        highs[k] = closes[k] + 0.5
        lows[k] = closes[k] - 0.5
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": np.full(85, 1000.0)},
        index=idx,
    )


def test_nr7_strategy_and_backtest():
    df = _make_nr7_df()
    strat = Nr7BreakoutStrategy({"nr7_lookback": 7, "nr7_atr_stop_mult": 2.0, "max_stale_minutes": 600})
    sig = strat.evaluate("TEST", df.iloc[:-5])
    assert sig.status == SignalStatus.ENTRY
    assert sig.strategy_id == "nr7_breakout"

    bt = run_nr7_breakout_backtest(df, nr7_lookback=7, stop_atr_mult=2.0)
    assert bt.total_trades >= 1


def test_nr7_wired_into_coordinator_agent11_and_governor(tmp_path):
    acct = PaperAccount(initial_capital=10000.0, portfolio_file=tmp_path / "port.json")
    coord = PortfolioCoordinator(account=acct, risk_engine=RiskEngine(default_risk_pct=0.02), config={"enable_nr7_breakout": True})
    assert coord.is_strategy_enabled("nr7_breakout")

    assert "nr7_lookback" in PARAMETER_BOUNDS
    assert "nr7_atr_stop_mult" in PARAMETER_BOUNDS
    assert "nr7_lookback" in NR7_BREAKOUT_GRID

    ok, errs = audit_invariants({"account_risk_pct": 0.02, "nr7_lookback": 7, "nr7_atr_stop_mult": 2.0})
    assert ok, errs
    bad_ok, _ = audit_invariants({"account_risk_pct": 0.02, "nr7_lookback": 99})
    assert not bad_ok

    from agent.multi_strategy_optimizer import load_active_config
    cfg = dict(load_active_config())
    cfg["enable_nr7_breakout"] = True
    tasks = build_optimization_tasks(cfg)
    assert any(t["strategy"] == "nr7_breakout" for t in tasks)
