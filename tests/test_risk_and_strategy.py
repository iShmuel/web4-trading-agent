"""Unit tests for strategy math and risk engine boundaries."""

import numpy as np
import pandas as pd
import pytest

from core.risk_engine import RiskEngine, MIN_ALLOWED_RISK_PCT, MAX_ALLOWED_RISK_PCT
from core.strategy import compute_indicators, calculate_atr, calculate_ema


def test_risk_bounds_clamping():
    engine = RiskEngine()

    # Risk below 1% must be clamped to 1%
    clamped_low = engine.enforce_risk_bounds(0.005)
    assert clamped_low == MIN_ALLOWED_RISK_PCT

    # Risk above 5% must be clamped to 5%
    clamped_high = engine.enforce_risk_bounds(0.12)
    assert clamped_high == MAX_ALLOWED_RISK_PCT

    # Risk within 1%-5% remains unchanged
    valid_risk = engine.enforce_risk_bounds(0.03)
    assert valid_risk == 0.03


def test_position_sizing_dollar_risk():
    engine = RiskEngine(default_risk_pct=0.02)
    equity = 10000.0
    entry_price = 60000.0
    atr = 1000.0
    atr_multiplier = 2.0

    units, stop_price, dollar_risk = engine.calculate_position(
        equity=equity,
        entry_price=entry_price,
        atr=atr,
        atr_multiplier=atr_multiplier,
    )

    # Expected dollar risk: 10,000 * 0.02 = 200.0
    assert pytest.approx(dollar_risk, 0.01) == 200.0
    assert pytest.approx(stop_price, 0.01) == 58000.0

    # If stopped out, exact loss should be ~200
    actual_loss = units * (entry_price - stop_price)
    assert pytest.approx(actual_loss, 0.01) == dollar_risk


def test_trailing_stop_only_moves_up():
    engine = RiskEngine()
    current_atr = 500.0
    multiplier = 2.0

    initial_stop = 50000.0

    # If price rises, stop should ratchet up
    higher_price = 55000.0
    updated_stop = engine.update_trailing_stop(higher_price, current_atr, multiplier, initial_stop)
    assert updated_stop == 54000.0
    assert updated_stop > initial_stop

    # If price drops, stop MUST NOT move down
    lower_price = 51000.0
    declined_stop = engine.update_trailing_stop(lower_price, current_atr, multiplier, updated_stop)
    assert declined_stop == updated_stop
