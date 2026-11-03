"""Unit tests for Decision Journal settlement and Backtest next-bar fill mode."""

import json
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np
import pytest

from core.journal import log_decision, load_journal_entries, settle_journal, generate_journal_report
from core.backtest_engine import run_backtest, run_mean_reversion_backtest


def test_decision_journal_logging_and_report(tmp_path):
    journal_path = tmp_path / "test_journal.jsonl"

    # Log an entry decision
    log_decision(
        symbol="BTC/USDT",
        action="ENTER_LONG",
        strategy_id="momentum",
        reason="Donchian Breakout High",
        reference_price=80000.0,
        indicators={"macro_ema": 75000.0, "adx": 30.0},
        journal_path=journal_path,
    )

    # Log a blocked gate decision
    log_decision(
        symbol="NVDA",
        action="REJECTED",
        strategy_id="momentum",
        reason="Blocked by Macro Bear Gate ($206.93 < EMA200 $221.10)",
        reference_price=206.93,
        indicators={"macro_ema": 221.10},
        journal_path=journal_path,
    )

    entries = load_journal_entries(journal_path)
    assert len(entries) == 2
    assert entries[0]["symbol"] == "BTC/USDT"
    assert entries[1]["action"] == "REJECTED"

    # Generate report on unsettled entries
    report = generate_journal_report(journal_path)
    assert "Pending: 2" in report


def test_decision_journal_settlement(tmp_path):
    journal_path = tmp_path / "test_settle_journal.jsonl"
    start_dt = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)

    # Log entry at 10:00
    entry = log_decision(
        symbol="SOL/USDT",
        action="ENTER_LONG",
        strategy_id="momentum",
        reason="Breakout Entry",
        reference_price=100.0,
        journal_path=journal_path,
    )
    # Manually backdate entry timestamp
    entries = load_journal_entries(journal_path)
    entries[0]["timestamp"] = start_dt.isoformat()
    with open(journal_path, "w") as f:
        f.write(json.dumps(entries[0]) + "\n")

    # Generate forward market data: 30 bars (more than 24 bars)
    dates = pd.date_range(start_dt, periods=30, freq="1h", tz="UTC")
    # SOL rises from 100 to 110 (+10% gain)
    sol_df = pd.DataFrame({"open": [100.0] * 30, "close": np.linspace(100, 110, 30)}, index=dates)
    # BTC (benchmark) stays flat at 60000 (0% return)
    btc_df = pd.DataFrame({"open": [60000.0] * 30, "close": [60000.0] * 30}, index=dates)

    market_data = {"SOL/USDT": sol_df, "BTC/USDT": btc_df}

    settled_cnt, pending_cnt = settle_journal(market_data, holding_bars=24, journal_path=journal_path)
    assert settled_cnt == 1
    assert pending_cnt == 0

    settled_entries = load_journal_entries(journal_path)
    e = settled_entries[0]
    assert e["settled"] is True
    assert e["forward_return_pct"] > 0
    assert e["alpha_pct"] > 0
    assert e["effective_call"] is True

    # Audit report should reflect settled stats
    report = generate_journal_report(journal_path)
    assert "Settled: 1" in report
    assert "ENTER_LONG" in report


def test_backtest_next_open_fill_mode():
    """Verify that fill_mode='next_open' fills at bar i+1 open rather than bar i close."""
    dates = pd.date_range("2026-01-01", periods=60, freq="1h", tz="UTC")
    base = 100.0 + np.arange(60) * 1.0  # steady uptrend to trigger breakout

    # On bar 25, close is 125, but bar 26 opens with a gap up at 135
    opens = [b - 0.2 for b in base]
    highs = [b + 1.0 for b in base]
    lows = [b - 0.5 for b in base]
    closes = [b + 0.5 for b in base]

    opens[26] = 135.0  # Gap up on next bar

    df = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes}, index=dates)

    res_close = run_backtest(df, breakout_lookback=20, ema_trend_filter=30, fill_mode="close")
    res_next_open = run_backtest(df, breakout_lookback=20, ema_trend_filter=30, fill_mode="next_open")

    assert len(res_close.trades) > 0
    assert len(res_next_open.trades) > 0
    # The entry price in next_open should reflect the next bar open, not the breakout bar close
    assert res_close.trades[0].entry_price != res_next_open.trades[0].entry_price
