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


def test_coordinator_macro_bear_gate(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_port.json")
    account = PaperAccount(10000.0)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {"enable_regime_filter": True})

    now = datetime.now(timezone.utc)
    # Price is $50,000, but Macro 200 EMA is $60,000 (Bear Regime)
    mom_sig = TradingSignal(
        strategy_id="momentum",
        symbol="BTC/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=50000.0,
        indicators={"macro_ema": 60000.0, "adx": 30.0},
    )
    mr_sig = TradingSignal(
        strategy_id="mean_reversion",
        symbol="BTC/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.NO_TRADE,
        reference_price=50000.0,
    )

    chosen, reason = coordinator.reconcile_signals("BTC/USDT", mom_sig, mr_sig)
    assert chosen is None
    assert "Blocked by Macro Bear Gate" in reason


def test_coordinator_chop_gate(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_port.json")
    account = PaperAccount(10000.0)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {"enable_regime_filter": True, "adx_chop_threshold": 20.0})

    now = datetime.now(timezone.utc)
    # ADX is 15.0 (Choppy sideways range)
    mom_sig = TradingSignal(
        strategy_id="momentum",
        symbol="SOL/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=150.0,
        indicators={"macro_ema": 140.0, "adx": 15.0},
    )
    mr_sig = TradingSignal(
        strategy_id="mean_reversion",
        symbol="SOL/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=150.0,
        indicators={"adx": 15.0},
    )

    chosen, reason = coordinator.reconcile_signals("SOL/USDT", mom_sig, mr_sig)
    # Breakout is blocked, Mean Reversion is approved!
    assert chosen is not None
    assert chosen.strategy_id == "mean_reversion"


def test_coordinator_strong_trend_gate(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_port.json")
    account = PaperAccount(10000.0)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {"enable_regime_filter": True, "adx_trend_threshold": 25.0})

    now = datetime.now(timezone.utc)
    # ADX is 35.0 (Strong trend expansion)
    mom_sig = TradingSignal(
        strategy_id="momentum",
        symbol="NEAR/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=5.0,
        indicators={"macro_ema": 4.0, "adx": 35.0},
    )
    mr_sig = TradingSignal(
        strategy_id="mean_reversion",
        symbol="NEAR/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=5.0,
        indicators={"adx": 35.0},
    )

    chosen, reason = coordinator.reconcile_signals("NEAR/USDT", mom_sig, mr_sig)
    # Counter-trend MR suppressed, Momentum selected!
    assert chosen is not None
    assert chosen.strategy_id == "momentum"
