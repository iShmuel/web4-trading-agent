"""Unit tests for PortfolioCoordinator: signal reconciliation, conflict resolution, and allocation."""

from datetime import datetime, timezone
import pytest
import pandas as pd

from core.signals import TradingSignal, SignalDirection, SignalStatus
from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine


def test_coordinator_conflict_resolution_opposite_directions(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_port.json")
    account = PaperAccount(10000.0)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {})

    now = datetime.now(timezone.utc)
    # Momentum proposes LONG
    mom_sig = TradingSignal(
        strategy_id="momentum",
        symbol="BTC/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=60000.0,
        signal_strength=0.8,
    )
    # Mean Reversion proposes SHORT
    mr_sig = TradingSignal(
        strategy_id="mean_reversion",
        symbol="BTC/USDT",
        timestamp=now,
        direction=SignalDirection.SHORT,
        status=SignalStatus.ENTRY,
        reference_price=60000.0,
        signal_strength=0.7,
    )

    chosen, reason = coordinator.reconcile_signals("BTC/USDT", mom_sig, mr_sig)
    # Must reject both due to conflicting market state
    assert chosen is None
    assert "Conflict" in reason


def test_coordinator_reconcile_same_direction_picks_strongest(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_port.json")
    account = PaperAccount(10000.0)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {})

    now = datetime.now(timezone.utc)
    mom_sig = TradingSignal(
        strategy_id="momentum",
        symbol="ETH/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=3000.0,
        signal_strength=0.5,
    )
    mr_sig = TradingSignal(
        strategy_id="mean_reversion",
        symbol="ETH/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=3000.0,
        signal_strength=0.9,  # higher strength
    )

    chosen, reason = coordinator.reconcile_signals("ETH/USDT", mom_sig, mr_sig)
    assert chosen is not None
    assert chosen.strategy_id == "mean_reversion"
    assert "selected MeanReversion" in reason


def test_coordinator_cross_strategy_position_safety(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_port.json")
    account = PaperAccount(10000.0)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {"atr_multiplier": 2.5})

    # Strategy A (Momentum) opened LONG position
    account.open_position(
        symbol="SOL/USDT",
        entry_price=100.0,
        units=5.0,
        stop_loss=90.0,
        dollar_risk=50.0,
        side="LONG",
        strategy_id="momentum",
    )

    # Now Strategy B tries to validate a SHORT entry proposal on the same symbol
    verdict = risk.validate_proposed_entry(
        symbol="SOL/USDT",
        side="SHORT",
        strategy_id="mean_reversion",
        entry_price=100.0,
        suggested_stop=110.0,
        atr=5.0,
        atr_multiplier=2.0,
        equity=account.get_total_equity(),
        cash=account.cash,
        active_positions=account.positions,
    )

    assert verdict.approved is False
    assert "conflict" in verdict.reason.lower()
