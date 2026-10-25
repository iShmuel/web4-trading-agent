"""Tests for per-asset profiles and the Momentum partial take-profit."""

import pandas as pd
import pytest

from core.coordinator import PortfolioCoordinator, resolve_symbol_config
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.signals import SignalStatus
from strategies.momentum import MomentumStrategy

BASE = {
    "timeframe": "1h",
    "breakout_lookback": 24,
    "ema_trend_filter": 50,
    "atr_multiplier": 3.0,
    "account_risk_pct": 0.02,
    "max_concurrent_positions": 3,
    "asset_profiles": {
        "fast": {
            "symbols": ["BTC/USDT"],
            "timeframe": "15m",
            "breakout_lookback": 16,
            "ema_trend_filter": 32,
            "atr_multiplier": 2.0,
            "take_profit_atr_mult": 2.5,
            "take_profit_ratio": 0.5,
            # A profile must never be able to change risk bounds:
            "account_risk_pct": 0.50,
            "max_concurrent_positions": 99,
        }
    },
}


def test_profile_overrides_strategy_settings_but_not_risk():
    cfg = resolve_symbol_config(BASE, "BTC/USDT")
    assert cfg["profile_name"] == "fast"
    assert cfg["timeframe"] == "15m"
    assert cfg["breakout_lookback"] == 16
    assert cfg["take_profit_ratio"] == 0.5
    assert cfg["account_risk_pct"] == 0.02
    assert cfg["max_concurrent_positions"] == 3
    assert "asset_profiles" not in cfg


def test_unlisted_symbol_uses_defaults():
    cfg = resolve_symbol_config(BASE, "ETH/USDT")
    assert cfg["profile_name"] == "default"
    assert cfg["breakout_lookback"] == 24


def test_profiles_can_be_disabled():
    cfg = resolve_symbol_config({**BASE, "use_asset_profiles": False}, "BTC/USDT")
    assert cfg["profile_name"] == "default"
    assert cfg["breakout_lookback"] == 24


def test_coordinator_builds_per_symbol_engines(tmp_path):
    account = PaperAccount(10000.0, portfolio_file=tmp_path / "p.json")
    coord = PortfolioCoordinator(account, RiskEngine(), BASE)
    assert coord._engines_for("BTC/USDT")[1].breakout_lookback == 16
    assert coord._engines_for("ETH/USDT")[1].breakout_lookback == 24


def _flat_df(price: float, n: int = 120) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"open": price, "high": price * 1.001, "low": price * 0.999, "close": price, "volume": 1.0}, index=idx)


def test_momentum_partial_take_profit_triggers_once():
    strat = MomentumStrategy({"take_profit_ratio": 0.5, "max_stale_minutes": 0})
    pos = {"entry_price": 100.0, "trailing_stop": 90.0, "side": "LONG", "tp_target": 105.0, "tp_ratio": 0.5, "tp_hit": False}

    below = strat.evaluate("X", _flat_df(104.0), existing_position=pos)
    assert below.status == SignalStatus.HOLD

    above = strat.evaluate("X", _flat_df(106.0), existing_position=pos)
    assert above.status == SignalStatus.PARTIAL_EXIT
    assert above.take_profit_ratio == 0.5

    pos["tp_hit"] = True
    after = strat.evaluate("X", _flat_df(108.0), existing_position=pos)
    assert after.status == SignalStatus.HOLD


def test_set_position_fields_persists_and_protects_accounting(tmp_path):
    f = tmp_path / "p.json"
    account = PaperAccount(10000.0, portfolio_file=f)
    account.open_position("BTC/USDT", 100.0, 1.0, 95.0, 5.0, strategy_id="momentum")
    account.set_position_fields("BTC/USDT", tp_target=110.0, tp_hit=False)
    assert PaperAccount(10000.0, portfolio_file=f).get_position("BTC/USDT")["tp_target"] == 110.0
    with pytest.raises(ValueError):
        account.set_position_fields("BTC/USDT", units=999.0)
