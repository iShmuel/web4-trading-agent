"""Unit tests for Institutional Alpha Upgrades:
1. Volatility Targeting & Half-Kelly Sizing
2. Multi-Timeframe Confluence Engine
3. Derivatives Funding Rate Radar
4. Relative Strength Alpha Prioritization
"""

from datetime import datetime, timezone
import pytest
import pandas as pd
import numpy as np

from core.signals import TradingSignal, SignalDirection, SignalStatus
from core.risk_engine import RiskEngine
from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.strategy import calculate_multi_timeframe_confluence, calculate_relative_strength


def test_volatility_targeting_bounds():
    risk = RiskEngine(default_risk_pct=0.03)

    # 1. Normal volatility (ATR = $2 on $100 price = 2% ATR ratio)
    normal_risk = risk.compute_volatility_adjusted_risk(
        base_risk_pct=0.03, current_atr=2.0, entry_price=100.0, atr_ratio_baseline=0.025
    )
    assert normal_risk == 0.03

    # 2. Extreme volatility shock (ATR = $8 on $100 price = 8% ATR ratio)
    high_vol_risk = risk.compute_volatility_adjusted_risk(
        base_risk_pct=0.03, current_atr=8.0, entry_price=100.0, atr_ratio_baseline=0.025
    )
    # Risk should scale down significantly
    assert high_vol_risk < 0.03
    # Invariant Rule 3: strictly clamped >= 1.0%
    assert high_vol_risk >= 0.01


def test_half_kelly_sizing():
    risk = RiskEngine()

    # 55% Win Rate, 2.0 Win/Loss Ratio
    kelly_risk = risk.compute_kelly_risk(base_risk_pct=0.02, win_rate=0.55, win_loss_ratio=2.0)
    assert 0.01 <= kelly_risk <= 0.05

    # Edge case: Negative expectation
    loss_kelly = risk.compute_kelly_risk(base_risk_pct=0.02, win_rate=0.20, win_loss_ratio=0.5)
    assert loss_kelly == 0.01


def test_multi_timeframe_confluence():
    dates = pd.date_range("2026-01-01", periods=60, freq="1h", tz="UTC")
    closes = np.linspace(100, 150, 60)
    df = pd.DataFrame({
        "close": closes,
        "high": closes + 1.0,
        "low": closes - 1.0,
        "breakout_high": closes - 2.0,
        "ema_filter": closes - 5.0,
        "macro_ema": closes - 10.0,
    }, index=dates)

    has_conf, conf_score, desc = calculate_multi_timeframe_confluence(df)
    assert has_conf is True
    assert conf_score >= 0.90
    assert "Triple Confluence" in desc


def test_funding_rate_overheat_gate(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_port.json")
    account = PaperAccount(10000.0)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {"enable_regime_filter": True})

    now = datetime.now(timezone.utc)
    # Signal with overheated funding rate (+0.08% per 8h)
    mom_sig = TradingSignal(
        strategy_id="momentum",
        symbol="BTC/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=80000.0,
        indicators={"macro_ema": 70000.0, "adx": 30.0, "funding_regime": "OVERHEATED_LONGS", "funding_rate_pct": 0.08},
    )
    mr_sig = TradingSignal(
        strategy_id="mean_reversion",
        symbol="BTC/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.NO_TRADE,
        reference_price=80000.0,
    )

    chosen, reason = coordinator.reconcile_signals("BTC/USDT", mom_sig, mr_sig)
    assert chosen is None
    assert "Overheated Funding Rate" in reason


def test_relative_strength_calculation():
    dates = pd.date_range("2026-01-01", periods=30, freq="1h", tz="UTC")
    # Asset gained 10%
    asset = pd.Series(np.linspace(100, 110, 30), index=dates)
    # Benchmark was flat
    bench = pd.Series(np.linspace(100, 100, 30), index=dates)

    rs = calculate_relative_strength(asset, bench, lookback=24)
    assert rs > 0.05
