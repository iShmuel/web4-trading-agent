"""Unit tests for standard TradingSignal contract and serialization."""

from datetime import datetime, timezone
import pytest

from core.signals import (
    TradingSignal,
    SignalDirection,
    SignalStatus,
    create_no_trade_signal,
)


def test_signal_properties():
    now = datetime.now(timezone.utc)
    sig = TradingSignal(
        strategy_id="momentum",
        symbol="BTC/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=65000.0,
        suggested_stop=63000.0,
        suggested_take_profit=69000.0,
        estimated_fee_pct=0.0006,
        estimated_slippage_pct=0.0004,
    )

    assert sig.is_actionable is True
    assert pytest.approx(sig.estimated_cost_pct, 0.00001) == 0.0020  # 0.20% roundtrip


def test_no_trade_signal_helper():
    sig = create_no_trade_signal(
        strategy_id="mean_reversion",
        symbol="ETH/USDT",
        price=2500.0,
        reason="ADX indicates strong trend",
    )

    assert sig.direction == SignalDirection.FLAT
    assert sig.status == SignalStatus.NO_TRADE
    assert sig.is_actionable is False
    assert "strong trend" in sig.no_trade_reason


def test_signal_serialization():
    now = datetime.now(timezone.utc)
    sig = TradingSignal(
        strategy_id="momentum",
        symbol="NVDA",
        timestamp=now,
        direction=SignalDirection.SHORT,
        status=SignalStatus.ENTRY,
        reference_price=200.0,
        suggested_stop=210.0,
    )

    data = sig.to_dict()
    assert data["symbol"] == "NVDA"
    assert data["direction"] == "SHORT"
    assert data["status"] == "ENTRY"
    assert data["reference_price"] == 200.0
