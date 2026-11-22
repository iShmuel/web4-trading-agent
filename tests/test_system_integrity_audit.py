"""Regression tests for Full System Integrity & Integration Audit fixes."""

import logging
from unittest.mock import MagicMock, patch

import pandas as pd

from core import data_loader
from core.coordinator import PortfolioCoordinator
from core.dynamic_router import select_rolling_strategy
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.signals import SignalDirection, create_no_trade_signal


def test_ccxt_exchange_client_is_cached_and_reused():
    """_get_ccxt_exchange must cache clients per (exchange_name, timeout) to avoid memory/CPU bloat."""
    data_loader._CCXT_CLIENTS.clear()
    fake_cls = MagicMock(side_effect=lambda cfg: {"cfg": cfg, "id": object()})
    with patch.object(data_loader.ccxt, "binance", fake_cls, create=True):
        c1 = data_loader._get_ccxt_exchange("binance", timeout=30000)
        c2 = data_loader._get_ccxt_exchange("binance", timeout=30000)
        c3 = data_loader._get_ccxt_exchange("binance", timeout=8000)
    assert c1 is c2
    assert c1 is not c3
    assert fake_cls.call_count == 2
    data_loader._CCXT_CLIENTS.clear()


def test_reconcile_signals_prefers_active_strategy_reason_over_paused(tmp_path):
    """When no strategy enters, reconcile_signals should report an active strategy's reason, not a paused one."""
    acct = PaperAccount(initial_capital=10000.0, portfolio_file=tmp_path / "pf.json")
    risk = RiskEngine()
    coord = PortfolioCoordinator(account=acct, risk_engine=risk, config={"enable_regime_filter": False})

    mom_paused = create_no_trade_signal("momentum", "SOL/USDT", 70.0, "Paused by Regime Playbook B (Macro=BEAR)")
    mr_paused = create_no_trade_signal("mean_reversion", "SOL/USDT", 70.0, "Altcoin Mean-Reversion Suppressed (Momentum Only)")
    active_extra = create_no_trade_signal("bear_chandelier_short", "SOL/USDT", 70.0, "Waiting for Bear Rally Z>=1.5 (Z=0.42)")

    chosen, reason = coord.reconcile_signals("SOL/USDT", mom_paused, mr_paused, extra_signals=[active_extra])
    assert chosen is None
    assert "Waiting for Bear Rally" in reason


def test_macro_radar_config_flags_gate_network_evaluations(tmp_path):
    """Disabling enable_macro_event_radar and enable_vix_regime_filter must skip both radar evaluations."""
    acct = PaperAccount(initial_capital=10000.0, portfolio_file=tmp_path / "pf.json")
    risk = RiskEngine()
    cfg = {
        "enable_macro_event_radar": False,
        "enable_vix_regime_filter": False,
        "enable_regime_filter": False,
        "enable_nyse_session_gate": False,
        "enable_sec_8k_gate": False,
        "enable_sec_fundamental_gate": False,
        "enable_wash_and_streak_guard": False,
        "enable_confluence_gate": False,
        "enable_local_llm_preflight": False,
        "enable_dynamic_rolling_router": False,
        "enable_macro_regime_playbook": False,
        "enable_funding_radar": False,
        "enable_hurst_gate": False,
        "enable_volatility_targeting": True,
    }
    coord = PortfolioCoordinator(account=acct, risk_engine=risk, config=cfg)
    coord.polymarket_radar.evaluate = MagicMock(side_effect=AssertionError("Polymarket radar should be gated off"))
    coord.fred_radar.evaluate = MagicMock(side_effect=AssertionError("FRED radar should be gated off"))

    idx = pd.date_range("2026-01-01", periods=260, freq="1h", tz="UTC")
    closes = [100.0 + i * 0.25 for i in range(260)]
    df = pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [10000.0] * 260,
        },
        index=idx,
    )
    # Trigger a deterministic entry signal through process_symbol
    decision = coord.process_symbol("SOL/USDT", df, {"SOL/USDT": closes[-1]})
    assert decision is not None
    assert coord.polymarket_radar.evaluate.call_count == 0
    assert coord.fred_radar.evaluate.call_count == 0


def test_dynamic_router_logs_warning_on_exception(caplog):
    """select_rolling_strategy must log a warning instead of silently swallowing exceptions."""
    idx = pd.date_range("2026-01-01", periods=300, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {"open": [10.0] * 300, "high": [11.0] * 300, "low": [9.0] * 300, "close": [10.0] * 300, "volume": [100.0] * 300},
        index=idx,
    )
    with patch("core.backtest_engine.run_basis_yield_backtest", side_effect=RuntimeError("boom")):
        with caplog.at_level(logging.WARNING, logger="core.dynamic_router"):
            route = select_rolling_strategy(
                symbol="SOL/USDT",
                df=df,
                sym_cfg={},
                config={},
                is_strategy_enabled_fn=lambda _: False,
            )
    assert route == "all"
    assert any("Strategy routing failed for SOL/USDT" in rec.message for rec in caplog.records)


def test_dynamic_router_skips_suppressed_altcoin_mean_reversion():
    """When crypto_altcoin_momentum_only=True, router must never select mean_reversion on altcoins."""
    idx = pd.date_range("2026-01-01", periods=300, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {"open": [10.0] * 300, "high": [11.0] * 300, "low": [9.0] * 300, "close": [10.0] * 300, "volume": [100.0] * 300},
        index=idx,
    )
    fake_mr = MagicMock(total_trades=10, profit_factor=3.5, sharpe_ratio=4.0)
    with patch("core.backtest_engine.run_mean_reversion_backtest", return_value=fake_mr) as mr_mock:
        route = select_rolling_strategy(
            symbol="SOL/USDT",
            df=df,
            sym_cfg={},
            config={"crypto_altcoin_momentum_only": True, "dynamic_router_min_pf": 1.4, "dynamic_router_min_trades": 6},
            is_strategy_enabled_fn=lambda sid: sid == "mean_reversion",
            allowed_playbook={"mean_reversion", "basis_yield"},
        )
    assert mr_mock.call_count == 0
    assert route == "basis_yield"
