"""Unit tests for Tier-1 Institutional Quantitative Features:
1. Smart Order Routing (Maker execution, fee reduction, 0 slippage)
2. Liquidation Cascade & Absorption Wick Detector
3. Statistical Regime Classification (Kaufman ER, Volatility expansion)
4. Hierarchical Risk Parity (HRP) covariance clustering & weighting
5. Market-Neutral Statistical Arbitrage Pairs Engine
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from core.strategy import detect_liquidation_absorption, calculate_statistical_regime
from core.risk_engine import compute_hierarchical_risk_parity_weights, RiskEngine
from strategies.stat_arb_pairs import StatArbPairsStrategy, PairTradeSignal


def test_liquidation_absorption_bullish_and_bearish():
    # Construct synthetic candles with an absorption wick
    dates = pd.date_range("2026-01-01", periods=30, freq="1h")
    df = pd.DataFrame({
        "open": [100.0] * 30,
        "high": [102.0] * 30,
        "low": [98.0] * 30,
        "close": [100.0] * 30,
        "volume": [1000.0] * 30,
    }, index=dates)

    # Inject extreme volume and long lower shadow on bar 25
    df.iloc[25, df.columns.get_loc("low")] = 85.0
    df.iloc[25, df.columns.get_loc("open")] = 98.0
    df.iloc[25, df.columns.get_loc("close")] = 100.0
    df.iloc[25, df.columns.get_loc("high")] = 101.0
    df.iloc[25, df.columns.get_loc("volume")] = 5000.0  # 5x volume surge

    signals = detect_liquidation_absorption(df, volume_mult=2.0, wick_ratio=0.50)
    assert signals.iloc[25] == "BULLISH_ABSORPTION"

    # Inject extreme volume and long upper shadow on bar 28
    df.iloc[28, df.columns.get_loc("high")] = 115.0
    df.iloc[28, df.columns.get_loc("open")] = 101.0
    df.iloc[28, df.columns.get_loc("close")] = 99.0
    df.iloc[28, df.columns.get_loc("low")] = 98.0
    df.iloc[28, df.columns.get_loc("volume")] = 5000.0

    signals2 = detect_liquidation_absorption(df, volume_mult=2.0, wick_ratio=0.50)
    assert signals2.iloc[28] == "BEARISH_ABSORPTION"


def test_calculate_statistical_regime():
    dates = pd.date_range("2026-01-01", periods=40, freq="1h")
    # Trending series: steady monotonic rise
    trend_df = pd.DataFrame({
        "open": np.linspace(100, 150, 40),
        "high": np.linspace(101, 151, 40),
        "low": np.linspace(99, 149, 40),
        "close": np.linspace(100, 150, 40),
        "volume": [1000.0] * 40,
    }, index=dates)

    regime, er = calculate_statistical_regime(trend_df, window=20)
    assert regime == "TREND_EXPANSION"
    assert er > 0.40

    # Choppy series: oscillating back and forth
    chop_closes = [100.0 if i % 2 == 0 else 101.0 for i in range(40)]
    chop_df = pd.DataFrame({
        "open": chop_closes,
        "high": [c + 1.0 for c in chop_closes],
        "low": [c - 1.0 for c in chop_closes],
        "close": chop_closes,
        "volume": [1000.0] * 40,
    }, index=dates)

    regime_chop, er_chop = calculate_statistical_regime(chop_df, window=20)
    assert regime_chop == "STATIONARY_CHOP"
    assert er_chop < 0.30


def test_hierarchical_risk_parity_weights():
    # 3 assets: Asset A & B highly correlated, Asset C uncorrelated
    np.random.seed(42)
    n = 100
    ret_a = np.random.normal(0.001, 0.02, n)
    ret_b = ret_a + np.random.normal(0.0, 0.005, n)  # 0.9+ correlation with A
    ret_c = np.random.normal(0.001, 0.015, n)  # uncorrelated stock

    returns_df = pd.DataFrame({"SOL": ret_a, "AVAX": ret_b, "AMD": ret_c})
    base_w = {"SOL": 0.03, "AVAX": 0.03, "AMD": 0.025}

    hrp_weights = compute_hierarchical_risk_parity_weights(returns_df, base_weights=base_w)
    assert "SOL" in hrp_weights
    assert "AVAX" in hrp_weights
    assert "AMD" in hrp_weights

    # Invariant: weights must be strictly clamped between 1% and 5%
    for sym, w in hrp_weights.items():
        assert 0.01 <= w <= 0.05, f"{sym} weight {w} outside [1%, 5%] bounds"


def test_stat_arb_pairs_engine():
    dates = pd.date_range("2026-01-01", periods=60, freq="1h")
    # Base price series for B
    price_b = 50.0 + np.sin(np.linspace(0, 10, 60)) * 5.0
    # Cointegrated series A = 2 * B
    price_a = 2.0 * price_b

    # Add a massive divergence at the end (A drops, creating an extreme negative Z-score)
    price_a[-1] -= 20.0

    df_a = pd.DataFrame({"open": price_a, "high": price_a + 1, "low": price_a - 1, "close": price_a}, index=dates)
    df_b = pd.DataFrame({"open": price_b, "high": price_b + 1, "low": price_b - 1, "close": price_b}, index=dates)

    engine = StatArbPairsStrategy(config={"stat_arb_lookback": 24, "stat_arb_entry_z": 2.0})
    signal = engine.evaluate_pair("SOL", "AVAX", df_a, df_b)

    assert isinstance(signal, PairTradeSignal)
    assert signal.action == "LONG_A_SHORT_B"
    assert signal.z_score <= -2.0
