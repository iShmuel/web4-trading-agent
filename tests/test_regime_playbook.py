"""Tests for Macro Regime Playbook B in core.coordinator.PortfolioCoordinator."""

from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd

from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from strategies.mean_reversion import MeanReversionStrategy
from strategies.stat_arb_pairs import PairTradeSignal, StatArbPairsStrategy
from strategies.volatility_squeeze import VolatilitySqueezeStrategy


def _make_daily_ohlcv(start_price: float, end_price: float, days: int = 30, last_day_spike: float = 1.0) -> pd.DataFrame:
    """Create hourly OHLCV over `days` calendar days with linear drift from start_price to end_price.

    `last_day_spike` multiplies only the final incomplete day to verify causal shift(1) ignores it.
    """
    idx = pd.date_range("2025-01-01", periods=days * 24, freq="1h", tz="UTC")
    prices = np.linspace(start_price, end_price, len(idx))
    # Apply spike only to the very last day's bars
    prices[-24:] = prices[-24:] * last_day_spike
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices * 1.005,
            "low": prices * 0.995,
            "close": prices,
            "volume": np.full(len(idx), 50000.0),
        },
        index=idx,
    )


def _make_coordinator(tmp_path, extra_cfg=None) -> PortfolioCoordinator:
    cfg = {
        "enable_macro_regime_playbook": True,
        "macro_regime_lookback_days": 20,
        "macro_regime_bull_pct": 4.0,
        "macro_regime_bear_pct": -4.0,
        "enable_dynamic_rolling_router": True,
        "enable_momentum": False,
        "enable_mean_reversion": True,
        "enable_volatility_squeeze": True,
        "enable_trend_pullback": False,
        "enable_vwap_reversion": False,
        "enable_nr7_breakout": False,
        "enable_range_envelope": False,
        "enable_pairs_trading": True,
        "enable_basis_yield": False,
        "enable_nyse_session_gate": False,
        "enable_sec_8k_gate": False,
        "enable_sec_fundamental_gate": False,
        "enable_wash_and_streak_guard": False,
        "enable_drift_rebalancer": False,
        "enable_macro_event_radar": False,
        "enable_vix_regime_filter": False,
        "enable_local_llm_preflight": False,
    }
    if extra_cfg:
        cfg.update(extra_cfg)
    acct = PaperAccount(initial_capital=10000.0, portfolio_file=tmp_path / "paper.json")
    risk = RiskEngine(max_open_positions=8)
    return PortfolioCoordinator(config=cfg, account=acct, risk_engine=risk)


def test_macro_playbook_regime_classification_and_causality(tmp_path):
    coord = _make_coordinator(tmp_path)

    # BULL: +12% over 30d (~+8% over 20d), even if today's incomplete day crashes -30%
    bull_data = {
        "BNB/USDT": _make_daily_ohlcv(500.0, 560.0, days=30, last_day_spike=0.70),
        "AAPL": _make_daily_ohlcv(200.0, 224.0, days=30, last_day_spike=0.70),
    }
    regime, ret = coord.get_macro_playbook_regime(market_data=bull_data, force_refresh=True)
    assert regime == "BULL"
    assert ret >= 4.0

    # CHOPPY: +1% over 30d
    chop_data = {
        "BNB/USDT": _make_daily_ohlcv(500.0, 505.0, days=30),
        "AAPL": _make_daily_ohlcv(200.0, 202.0, days=30),
    }
    regime, ret = coord.get_macro_playbook_regime(market_data=chop_data, force_refresh=True)
    assert regime == "CHOPPY"
    assert -4.0 < ret < 4.0

    # BEAR: -15% over 30d (~-10% over 20d), even if today's incomplete day spikes +40%
    bear_data = {
        "BNB/USDT": _make_daily_ohlcv(500.0, 425.0, days=30, last_day_spike=1.40),
        "AAPL": _make_daily_ohlcv(200.0, 170.0, days=30, last_day_spike=1.40),
    }
    regime, ret = coord.get_macro_playbook_regime(market_data=bear_data, force_refresh=True)
    assert regime == "BEAR"
    assert ret <= -4.0


def test_playbook_b_allowed_strategies(tmp_path):
    coord = _make_coordinator(tmp_path)
    assert coord.get_playbook_allowed_strategies("BULL") == {
        StatArbPairsStrategy.STRATEGY_ID,
        "basis_yield",
    }
    assert coord.get_playbook_allowed_strategies("CHOPPY") == {
        VolatilitySqueezeStrategy.STRATEGY_ID,
        "basis_yield",
    }
    assert coord.get_playbook_allowed_strategies("BEAR") == {
        MeanReversionStrategy.STRATEGY_ID,
        StatArbPairsStrategy.STRATEGY_ID,
        "basis_yield",
    }


def test_router_respects_playbook_b_in_bull_regime(tmp_path):
    coord = _make_coordinator(tmp_path)
    bull_data = {
        "BNB/USDT": _make_daily_ohlcv(500.0, 580.0, days=30),
        "AAPL": _make_daily_ohlcv(200.0, 232.0, days=30),
    }
    coord._latest_market_data = bull_data
    coord.get_macro_playbook_regime(market_data=bull_data, force_refresh=True)

    # In BULL, directional MR & VolSqueeze are paused -> crypto routes to basis_yield, stocks to cash
    sym_cfg = coord.symbol_config("AAPL")
    assert coord._resolve_dynamic_rolling_strategy("AAPL", bull_data["AAPL"], sym_cfg) == "cash"
    sym_cfg_bnb = coord.symbol_config("BNB/USDT")
    assert coord._resolve_dynamic_rolling_strategy("BNB/USDT", bull_data["BNB/USDT"], sym_cfg_bnb) == "basis_yield"


def test_stat_arb_pairs_paused_in_choppy_but_close_pair_still_executes(tmp_path):
    coord = _make_coordinator(tmp_path)
    chop_data = {
        "BNB/USDT": _make_daily_ohlcv(500.0, 504.0, days=30),
        "AAPL": _make_daily_ohlcv(200.0, 201.0, days=30),
        "SMCI": _make_daily_ohlcv(40.0, 41.0, days=30),
        "IREN": _make_daily_ohlcv(10.0, 10.2, days=30),
    }

    # 1. New entry signal in CHOPPY must be REJECTED by Playbook B
    entry_sig = PairTradeSignal(
        pair_id="SMCI:IREN",
        asset_a="SMCI",
        asset_b="IREN",
        action="LONG_A_SHORT_B",
        timestamp=datetime(2025, 2, 1, 12, 0, tzinfo=timezone.utc),
        z_score=-2.6,
        spread=-0.12,
        hedge_ratio=1.0,
        half_life=18.0,
        correlation=0.82,
        reason="Test entry",
    )
    with patch.object(coord.pairs_strategy, "scan_dynamic_pairs", return_value=[entry_sig]):
        report = coord.run_cycle(chop_data, now=datetime(2025, 2, 1, 12, 0, tzinfo=timezone.utc))

    assert report.macro_regime == "CHOPPY"
    pair_decisions = [d for d in report.decisions if d.strategy_id == "stat_arb_pairs"]
    assert len(pair_decisions) == 1
    assert pair_decisions[0].action == "REJECTED"
    assert "Regime Playbook B" in pair_decisions[0].reason
    assert not coord.account.has_position("SMCI")

    # 2. Existing open pair position MUST still close normally in CHOPPY
    pos_a = coord.account.open_position(
        "SMCI",
        units=10.0,
        entry_price=41.0,
        stop_loss=38.0,
        dollar_risk=30.0,
        strategy_id="stat_arb_pairs",
        side="LONG",
    )
    pos_a["pair_id"] = "SMCI:IREN"
    pos_b = coord.account.open_position(
        "IREN",
        units=40.0,
        entry_price=10.2,
        stop_loss=11.0,
        dollar_risk=32.0,
        strategy_id="stat_arb_pairs",
        side="SHORT",
    )
    pos_b["pair_id"] = "SMCI:IREN"
    close_sig = PairTradeSignal(
        pair_id="SMCI:IREN",
        asset_a="SMCI",
        asset_b="IREN",
        action="CLOSE_PAIR",
        timestamp=datetime(2025, 2, 1, 13, 0, tzinfo=timezone.utc),
        z_score=0.1,
        spread=0.01,
        hedge_ratio=1.0,
        half_life=18.0,
        correlation=0.82,
        reason="Mean reversion target reached",
    )
    with patch.object(coord.pairs_strategy, "scan_dynamic_pairs", return_value=[close_sig]):
        close_report = coord.run_cycle(chop_data, now=datetime(2025, 2, 1, 13, 0, tzinfo=timezone.utc))

    close_decisions = [d for d in close_report.decisions if d.strategy_id == "stat_arb_pairs"]
    assert any(d.action == "CLOSE_PAIR" for d in close_decisions)
    assert not coord.account.has_position("SMCI")
    assert not coord.account.has_position("IREN")
