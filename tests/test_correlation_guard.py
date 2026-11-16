"""Unit & integration tests for Portfolio Correlation & Sector-Cluster Risk Guard."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine


def test_risk_engine_blocks_correlated_sector_cluster():
    re = RiskEngine(max_open_positions=10, max_correlated_positions=2, correlation_threshold=0.75)
    active = {
        "NVDA": {"side": "LONG", "strategy_id": "momentum", "units": 10.0, "entry_price": 100.0},
        "AMD": {"side": "LONG", "strategy_id": "momentum", "units": 10.0, "entry_price": 100.0},
    }
    verdict_blocked = re.validate_proposed_entry(
        symbol="SMCI",
        side="LONG",
        strategy_id="momentum",
        entry_price=50.0,
        suggested_stop=46.0,
        atr=2.0,
        atr_multiplier=2.0,
        equity=10000.0,
        cash=8000.0,
        active_positions=active,
        correlation_map={"NVDA": 0.86, "AMD": 0.81},
    )
    assert verdict_blocked.approved is False
    assert "Portfolio Correlation Guard" in verdict_blocked.reason

    verdict_allowed = re.validate_proposed_entry(
        symbol="XLE",
        side="LONG",
        strategy_id="momentum",
        entry_price=50.0,
        suggested_stop=46.0,
        atr=2.0,
        atr_multiplier=2.0,
        equity=10000.0,
        cash=8000.0,
        active_positions=active,
        correlation_map={"NVDA": 0.25, "AMD": 0.18},
    )
    assert verdict_allowed.approved is True


def test_coordinator_passes_rolling_correlations(tmp_path: Path):
    acc = PaperAccount(initial_capital=10000.0, portfolio_file=tmp_path / "port.json")
    re = RiskEngine(max_open_positions=10, max_correlated_positions=2, correlation_threshold=0.75)
    coord = PortfolioCoordinator(acc, re, {"enable_correlation_guard": True})

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    idx = pd.date_range(end=now, periods=80, freq="1h", tz=timezone.utc)
    base = 100.0 + np.cumsum(np.sin(np.arange(80) * 0.3) + 0.2)
    coord._latest_market_data = {
        "NVDA": pd.DataFrame({"close": base}, index=idx),
        "AMD": pd.DataFrame({"close": base * 1.1}, index=idx),
    }
    assert coord.risk_engine.max_correlated_positions == 2
