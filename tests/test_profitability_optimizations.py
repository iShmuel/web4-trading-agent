"""Tests for Web4 Master Profitability & Engineering Optimizations.

Validates:
1. Concurrent batch OHLCV fetching via ThreadPoolExecutor.
2. SEC EDGAR Atom/XBRL disk caching with TTL and stale fallback.
3. Decision journal backward block reading and atomic rotation.
4. StatArbPairsStrategy expected edge-to-cost filter and round-trip fee gate.
5. PortfolioCoordinator dual-leg slippage application on pair fills.
"""

import gzip
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

from dataclasses import asdict
from core.data_loader import fetch_ohlcv_batch, filter_closed_bars
from core.journal import (
    JournalEntry,
    load_recent_settled_entries,
    rotate_journal,
)
from core.skill_intelligence_gate import (
    SecEdgar8KMonitor,
    SecFundamentalGate,
    _load_sec_disk_cache,
    _save_sec_disk_cache,
)
from strategies.stat_arb_pairs import StatArbPairsStrategy
from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine


def _make_dummy_ohlcv(symbol: str, bars: int = 100, price: float = 100.0) -> pd.DataFrame:
    now = datetime.now(timezone.utc)
    timestamps = [now - timedelta(hours=bars - i) for i in range(bars)]
    data = {
        "open": np.full(bars, price),
        "high": np.full(bars, price * 1.01),
        "low": np.full(bars, price * 0.99),
        "close": np.full(bars, price),
        "volume": np.full(bars, 1000.0),
    }
    df = pd.DataFrame(data, index=pd.to_datetime(timestamps, utc=True))
    df.index.name = "timestamp"
    return df


def test_concurrent_batch_fetch(tmp_path, monkeypatch):
    """Test fetch_ohlcv_batch successfully dispatches concurrent fetches."""
    symbols = ["SYM_A", "SYM_B", "SYM_C"]
    
    def fake_fetch_ohlcv(symbol, **kwargs):
        return _make_dummy_ohlcv(symbol, bars=50, price=50.0)

    monkeypatch.setattr("core.data_loader.fetch_ohlcv", fake_fetch_ohlcv)
    results = fetch_ohlcv_batch(symbols, timeframe="1h", days=30, max_workers=3)
    assert len(results) == 3
    for s in symbols:
        assert s in results
        assert len(results[s]) == 50


def test_sec_edgar_disk_cache(tmp_path, monkeypatch):
    """Test SEC 8-K persistent disk cache load, save, TTL expiration, and fallback."""
    cache_dir = tmp_path / "cache_sec"
    monkeypatch.setattr("core.skill_intelligence_gate.SEC_CACHE_DIR", cache_dir)

    payload = [{"symbol": "NVDA", "form": "8-K", "items": ["2.02"], "filed_at": "2026-11-01T12:00:00Z"}]
    _save_sec_disk_cache("8k_NVDA.json", payload)

    # Fresh read within TTL
    loaded = _load_sec_disk_cache("8k_NVDA.json", max_age_seconds=3600)
    assert loaded == payload

    # Stale read beyond TTL
    stale = _load_sec_disk_cache("8k_NVDA.json", max_age_seconds=0.001)
    time.sleep(0.01)
    assert _load_sec_disk_cache("8k_NVDA.json", max_age_seconds=0.001) is None

    # SecEdgar8KMonitor reads disk cache without network call
    monitor = SecEdgar8KMonitor(sec_user_agent="")
    res = monitor.fetch_recent_8k_filings("NVDA")
    assert len(res) == 1
    assert res[0]["items"] == ["2.02"]


def test_decision_journal_rotation_and_tail_read(tmp_path):
    """Test journal rotation archives settled records to gzip and preserves active records."""
    journal_file = tmp_path / "decision_journal.jsonl"
    archive_dir = tmp_path / "decision_journal_archive"

    # Write 100 entries: 60 settled, 40 active
    now = datetime.now(timezone.utc)
    with open(journal_file, "w", encoding="utf-8") as f:
        for i in range(100):
            is_settled = i < 60
            entry = JournalEntry(
                timestamp=(now - timedelta(hours=100 - i)).isoformat(),
                symbol="BTC/USDT",
                strategy_id="momentum",
                action="BUY",
                reason=f"test decision {i}",
                reference_price=50000.0,
                settled=is_settled,
                settled_at=now.isoformat() if is_settled else None,
                forward_return_pct=1.5 if is_settled else None,
            )
            f.write(json.dumps(asdict(entry)) + "\n")

    # Test load_recent_settled_entries reads recent settled rows without reading whole file
    recent = load_recent_settled_entries(journal_path=journal_file, count=10)
    assert len(recent) == 10
    assert all(e.get("settled") for e in recent)

    # Rotate journal keeping settled records within 2 days (48h)
    rotate_res = rotate_journal(
        journal_path=journal_file,
        keep_days=2.0,
        force=True,
    )

    assert rotate_res["rotated"] is True
    assert rotate_res["archived_records"] > 0
    assert rotate_res["kept_records"] > 0
    assert rotate_res["archived_records"] + rotate_res["kept_records"] == 100

    # Verify gzip archive was created and contains valid JSONL
    arch_file = Path(rotate_res["archive_file"])
    assert arch_file.exists()
    with gzip.open(arch_file, "rt", encoding="utf-8") as f:
        archived_lines = f.readlines()
        assert len(archived_lines) == rotate_res["archived_records"]


def test_stat_arb_edge_cost_gate():
    """Test that StatArbPairsStrategy rejects trades when expected edge is smaller than round trip friction."""
    strat = StatArbPairsStrategy(
        config={
            "stat_arb_lookback": 30,
            "stat_arb_entry_z": 2.0,
            "stat_arb_exit_z": 0.5,
            "stat_arb_min_edge_cost_ratio": 2.5,
            "stat_arb_cost_per_side_pct": 0.0010,  # 0.10% per leg
        }
    )

    # Case 1: Negligible spread variance -> expected edge < 2.5x round trip cost
    np.random.seed(42)
    s_a_flat = pd.Series(100.0 + np.random.normal(0, 0.01, 50))
    s_b_flat = pd.Series(100.0 + np.random.normal(0, 0.01, 50))
    edge, cost, ratio = strat.estimate_expected_edge_vs_cost(
        series_a=s_a_flat,
        series_b=s_b_flat,
        beta=1.0,
        z_score=2.0,
    )
    assert ratio < 2.5

    # Case 2: Meaningful spread variance -> expected edge > 2.5x round trip cost
    s_a_vol = pd.Series(100.0 + np.random.normal(0, 5.0, 50))
    s_b_vol = pd.Series(100.0 + np.random.normal(0, 5.0, 50))
    edge_2, cost_2, ratio_2 = strat.estimate_expected_edge_vs_cost(
        series_a=s_a_vol,
        series_b=s_b_vol,
        beta=1.0,
        z_score=2.0,
    )
    assert ratio_2 > 2.5


def test_coordinator_pair_slippage_and_fee_attribution():
    """Test that PortfolioCoordinator accurately applies adverse slippage to both legs of a pair fill."""
    account = PaperAccount(initial_capital=10000.0)
    risk_engine = RiskEngine()
    config = {
        "watchlist": ["BTC/USDT", "ETH/USDT"],
        "taker_fee_pct": 0.0006,
        "slippage_pct": 0.0005,
    }
    coord = PortfolioCoordinator(account, risk_engine, config)

    # Test pair fill prices apply adverse slippage correctly
    # Leg 1: BUY at 50,000 -> fill = 50,000 * 1.0005 = 50,025
    fill_buy = coord._pair_fill_price(50000.0, "LONG", opening=True)
    assert fill_buy > 50000.0
    assert abs(fill_buy - 50025.0) < 0.01

    # Leg 2: SELL at 3,000 -> fill = 3,000 * (1 - 0.0005) = 2,998.50
    fill_sell = coord._pair_fill_price(3000.0, "SHORT", opening=True)
    assert fill_sell < 3000.0
    assert abs(fill_sell - 2998.50) < 0.01
