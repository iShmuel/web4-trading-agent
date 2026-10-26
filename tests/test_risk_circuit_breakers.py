"""Unit tests for centralized RiskEngine, circuit breakers, and kill switch logic."""

import pytest
from core.risk_engine import RiskEngine, MIN_ALLOWED_RISK_PCT, MAX_ALLOWED_RISK_PCT


def test_circuit_breaker_daily_loss_limit():
    engine = RiskEngine(daily_loss_limit_pct=0.05)
    starting_equity = 10000.0

    # Start of day
    healthy, _ = engine.check_circuit_breakers(starting_equity)
    assert healthy is True
    assert engine.is_halted is False

    # Moderate drawdown: 3% loss ($9,700) -> should not trigger breaker
    healthy, _ = engine.check_circuit_breakers(9700.0)
    assert healthy is True

    # Severe loss: 6% loss ($9,400) -> must trigger circuit breaker kill switch!
    healthy, reason = engine.check_circuit_breakers(9400.0)
    assert healthy is False
    assert engine.is_halted is True
    assert "Daily loss circuit breaker" in reason


def test_circuit_breaker_max_drawdown():
    # Set high daily loss limit so we isolate testing the max drawdown circuit breaker
    engine = RiskEngine(daily_loss_limit_pct=0.50, max_drawdown_limit_pct=0.15)
    # Peak at 20,000
    engine.check_circuit_breakers(20000.0)
    assert engine.peak_equity == 20000.0

    # Drawdown of 10% (18,000) -> allowed
    healthy, _ = engine.check_circuit_breakers(18000.0)
    assert healthy is True

    # Drawdown of 16% (16,800) -> breaches 15% max drawdown!
    healthy, reason = engine.check_circuit_breakers(16800.0)
    assert healthy is False
    assert engine.is_halted is True
    assert "Max drawdown circuit breaker" in reason


def test_single_asset_exposure_cap_scales_units():
    engine = RiskEngine(max_single_asset_exposure_pct=0.30)
    equity = 10000.0
    cash = 10000.0
    active_positions = {}

    # Propose trade where volatility sizing would allocate $5,000 (50% of equity)
    # Stop distance is small: atr=1.0, mult=2.0 -> stop_dist=2.0. dollar_risk=200 -> units=100.
    # At price=100, cost = 100 * 100 = $10,000 (exceeds 30% cap)
    verdict = engine.validate_proposed_entry(
        symbol="NVDA",
        side="LONG",
        strategy_id="momentum",
        entry_price=100.0,
        suggested_stop=98.0,
        atr=1.0,
        atr_multiplier=2.0,
        equity=equity,
        cash=cash,
        active_positions=active_positions,
    )

    assert verdict.approved is True
    # Max single asset cost: 30% of $10,000 = $3,000
    # Scaled units should be ~30 units (30 * 100 = 3000)
    assert verdict.suggested_units <= 30.0


def test_correlation_guard_caps_crypto_positions():
    engine = RiskEngine(max_crypto_positions=2, max_open_positions=3)
    active = {
        "BTC/USDT": {"units": 0.05, "entry_price": 60000.0, "strategy_id": "momentum"},
        "ETH/USDT": {"units": 1.0, "entry_price": 2500.0, "strategy_id": "momentum"},
    }
    # 3rd crypto should be blocked by Correlation Guard
    v_crypto = engine.validate_proposed_entry(
        symbol="SOL/USDT",
        side="LONG",
        strategy_id="momentum",
        entry_price=100.0,
        suggested_stop=95.0,
        atr=2.0,
        atr_multiplier=2.5,
        equity=10000.0,
        cash=4000.0,
        active_positions=active,
    )
    assert v_crypto.approved is False
    assert "Correlation Guard" in v_crypto.reason

    # But an equity (NVDA) should be permitted into the 3rd slot!
    v_stock = engine.validate_proposed_entry(
        symbol="NVDA",
        side="LONG",
        strategy_id="momentum",
        entry_price=200.0,
        suggested_stop=190.0,
        atr=4.0,
        atr_multiplier=2.5,
        equity=10000.0,
        cash=4000.0,
        active_positions=active,
    )
    assert v_stock.approved is True


def test_dynamic_scaling_scales_down_to_remaining_strategy_cap():
    engine = RiskEngine(
        max_strategy_allocation_pct=0.75,
        dynamic_scaling=True,
    )
    # Already committed $6,000 in momentum
    active = {
        "AVAX/USDT": {"units": 600.0, "entry_price": 10.0, "strategy_id": "momentum"},
    }
    # Equity: 10,000. Cap: 7,500. Remaining cap: 1,500.
    # Proposed order normally wants $3,000 (30 units @ 100)
    verdict = engine.validate_proposed_entry(
        symbol="SOL/USDT",
        side="LONG",
        strategy_id="momentum",
        entry_price=100.0,
        suggested_stop=95.0,
        atr=2.0,
        atr_multiplier=2.5,
        equity=10000.0,
        cash=4000.0,
        active_positions=active,
    )
    assert verdict.approved is True
    # Scaled down to fit within remaining $1,500
    assert verdict.suggested_units * 100.0 <= 1500.0
