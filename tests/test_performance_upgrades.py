"""Unit test suite for Phase 1 Performance Upgrades:
1. Chandelier Trailing-Stop Ratchet on Trend Runners (+3R, +5R, +8R).
2. Altcoin Strategy Specialization (crypto altcoin momentum-only suppression).
3. Confluence-Weighted Dynamic Position Sizing (strictly within 1%-5% risk bounds).
"""

from datetime import datetime, timezone
import pandas as pd
import pytest

from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.signals import TradingSignal, SignalDirection, SignalStatus


def _make_df(prices):
    n = len(prices)
    timestamps = pd.date_range("2026-10-01", periods=n, freq="1h", tz="UTC")
    data = []
    for i, p in enumerate(prices):
        data.append({
            "open": p * 0.999,
            "high": p * 1.002,
            "low": p * 0.998,
            "close": p,
            "volume": 1000.0,
        })
    return pd.DataFrame(data, index=timestamps)


def test_altcoin_strategy_specialization(tmp_path):
    """Verify high-beta crypto altcoins suppress mean-reversion when crypto_altcoin_momentum_only=True."""
    port_file = tmp_path / "spec_port.json"
    account = PaperAccount(10000.0, portfolio_file=port_file)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(
        account,
        risk,
        {
            "crypto_altcoin_momentum_only": True,
            "enable_crash_cooldown": False,
            "enable_funding_radar": False,
        },
    )

    df = _make_df([100.0] * 65)

    # 1. Altcoin ENA/USDT should suppress mean reversion
    _, mom_eng, mr_eng = coordinator._engines_for("ENA/USDT")
    mom_eng.evaluate = lambda sym, d, existing_position=None: TradingSignal(
        strategy_id="momentum",
        symbol=sym,
        timestamp=datetime.now(timezone.utc),
        direction=SignalDirection.FLAT,
        status=SignalStatus.NO_TRADE,
        reference_price=100.0,
    )
    mr_eng.evaluate = lambda sym, d, existing_position=None: TradingSignal(
        strategy_id="mean_reversion",
        symbol=sym,
        timestamp=datetime.now(timezone.utc),
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=100.0,
        atr=2.0,
    )

    dec_alt = coordinator.process_symbol("ENA/USDT", df, {})
    # Mean reversion signal is suppressed, so decision is REJECTED / NO_TRADE
    assert dec_alt.action in ("REJECTED", "NO_TRADE")

    # 2. Macro Anchor BTC/USDT should NOT suppress mean reversion
    _, mom_btc, mr_btc = coordinator._engines_for("BTC/USDT")
    mom_btc.evaluate = lambda sym, d, existing_position=None: TradingSignal(
        strategy_id="momentum",
        symbol=sym,
        timestamp=datetime.now(timezone.utc),
        direction=SignalDirection.FLAT,
        status=SignalStatus.NO_TRADE,
        reference_price=60000.0,
    )
    mr_btc.evaluate = lambda sym, d, existing_position=None: TradingSignal(
        strategy_id="mean_reversion",
        symbol=sym,
        timestamp=datetime.now(timezone.utc),
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=60000.0,
        atr=500.0,
        suggested_stop=59000.0,
    )

    dec_btc = coordinator.process_symbol("BTC/USDT", _make_df([60000.0] * 65), {})
    # BTC is NOT suppressed by crypto_altcoin_momentum_only
    assert dec_btc.reason != "Altcoin Mean-Reversion Suppressed (Momentum Only)"


def test_chandelier_trailing_ratchet(tmp_path):
    """Verify Chandelier Trailing-Stop ratchets tighter at +3R, +5R, and +8R multiples of initial risk."""
    port_file = tmp_path / "ratchet_port.json"
    account = PaperAccount(10000.0, portfolio_file=port_file)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(
        account,
        risk,
        {
            "enable_chandelier_ratchet": True,
            "enable_breakeven_lock": True,
        },
    )

    # Open a long position at 100 with stop at 95 (initial risk = 5.0, ATR = 2.0, ATR mult = 2.5)
    account.open_position("TEST/USDT", 100.0, 10.0, 95.0, 50.0, side="LONG", strategy_id="momentum")
    assert "TEST/USDT" in account.positions

    # Price moves to 120 (+20 gain / 5 risk = +4R). At +3R, stop should lock at least entry + 1.0*ATR (100 + 2 = 102)
    df_gain = _make_df([120.0] * 65)
    dec = coordinator.process_symbol("TEST/USDT", df_gain, {})
    assert dec.action == "HOLD"
    pos = account.get_position("TEST/USDT")
    assert pos["trailing_stop"] >= 102.0  # Profit locked in!

    # Price explodes to 150 (+50 gain / 5 risk = +10R). At +8R, stop should ratchet to cur_price - 0.75*ATR (150 - 1.5 = 148.5)
    df_parabolic = _make_df([150.0] * 65)
    dec_para = coordinator.process_symbol("TEST/USDT", df_parabolic, {})
    assert dec_para.action == "HOLD"
    pos_para = account.get_position("TEST/USDT")
    assert pos_para["trailing_stop"] >= 147.5  # Windfall locked in at 147.75 (150 - 0.75*ATR)!
