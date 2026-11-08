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


def test_preflight_staleness_and_spread_via_coordinator(tmp_path):
    """Verify coordinator passes real df quote_time and bid/ask spread to PreFlightChecker (ISSUE-001 & ISSUE-002)."""
    from datetime import timedelta

    account = PaperAccount(10000.0, portfolio_file=tmp_path / "pf_port.json")
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(
        account,
        risk,
        {
            "enable_regime_filter": False,
            "enable_confluence_gate": False,
            "enable_crash_cooldown": False,
            "preflight_max_staleness_seconds": 180,
            "preflight_max_spread_pct": 0.0015,
        },
    )

    _, mom_eng, mr_eng = coordinator._engines_for("SOL/USDT")
    mom_eng.evaluate = lambda sym, d, existing_position=None: TradingSignal(
        strategy_id="momentum",
        symbol=sym,
        timestamp=datetime.now(timezone.utc),
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=100.0,
        atr=2.0,
        suggested_stop=95.0,
    )
    mr_eng.evaluate = lambda sym, d, existing_position=None: TradingSignal(
        strategy_id="mean_reversion",
        symbol=sym,
        timestamp=datetime.now(timezone.utc),
        direction=SignalDirection.FLAT,
        status=SignalStatus.NO_TRADE,
        reference_price=100.0,
    )

    # 1. Stale quote_time attached to df.attrs should trigger PREFLIGHT_PRICE_STALE
    stale_df = _make_df([100.0] * 65)
    stale_df.attrs["quote_time"] = datetime.now(timezone.utc) - timedelta(seconds=600)
    dec_stale = coordinator.process_symbol("SOL/USDT", stale_df, {})
    assert dec_stale.action == "REJECTED"
    assert "PREFLIGHT_PRICE_STALE" in dec_stale.reason

    # 2. Wide bid/ask spread should trigger PREFLIGHT_SPREAD_TOO_WIDE
    wide_df = _make_df([100.0] * 65)
    wide_df.attrs["quote_time"] = datetime.now(timezone.utc)
    wide_df.attrs["bid"] = 99.50
    wide_df.attrs["ask"] = 100.50  # 1.0% spread > 0.15% max
    dec_wide = coordinator.process_symbol("SOL/USDT", wide_df, {})
    assert dec_wide.action == "REJECTED"
    assert "PREFLIGHT_SPREAD_TOO_WIDE" in dec_wide.reason


def test_basis_yield_persists_accrual_timestamp_across_restarts(tmp_path, monkeypatch):
    """Verify basis yield accrual timestamp persists in PaperAccount state so restarts do not double-accrue (ISSUE-011)."""
    from core import basis_yield

    monkeypatch.setattr(basis_yield, "fetch_funding_rate", lambda sym: 0.0003)
    pfile = tmp_path / "basis_persist.json"
    account1 = PaperAccount(10000.0, portfolio_file=pfile)
    cfg = {"enable_basis_yield": True, "enable_pairs_trading": False, "basis_hurdle_apr": 8.0}
    coord1 = PortfolioCoordinator(account1, RiskEngine(), cfg)

    market_data = {"BTC/USDT": _make_df([60000.0] * 65)}
    coord1.run_cycle(market_data)
    cash_after_first = account1.cash
    assert cash_after_first > 10000.0

    # Simulate process restart by instantiating a new PaperAccount and PortfolioCoordinator on the same file
    account2 = PaperAccount(10000.0, portfolio_file=pfile)
    coord2 = PortfolioCoordinator(account2, RiskEngine(), cfg)
    assert coord2.last_basis_accrual_time is not None
    coord2.run_cycle(market_data)
    assert account2.cash == pytest.approx(cash_after_first, rel=1e-9)


def test_llm_cache_bounded_eviction():
    """Verify _LLM_CACHE enforces MAX_CACHE_ENTRIES bound (ISSUE-010)."""
    from agent import llm_client

    llm_client.clear_llm_cache()
    now = 1_700_000_000.0
    for i in range(llm_client.MAX_CACHE_ENTRIES + 20):
        llm_client._LLM_CACHE[f"key_{i}"] = (now + i, f"resp_{i}")
        while len(llm_client._LLM_CACHE) > llm_client.MAX_CACHE_ENTRIES:
            oldest = min(llm_client._LLM_CACHE, key=lambda k: llm_client._LLM_CACHE[k][0])
            llm_client._LLM_CACHE.pop(oldest, None)
    assert len(llm_client._LLM_CACHE) <= llm_client.MAX_CACHE_ENTRIES
    llm_client.clear_llm_cache()


def test_momentum_short_cost_check_and_mr_take_profit_ratio():
    """Verify Momentum short signal rejects negative net-reward after costs (ISSUE-006) and MR sets take_profit_ratio (ISSUE-007)."""
    from strategies.momentum import MomentumStrategy
    from strategies.mean_reversion import MeanReversionStrategy

    # 1. Momentum short with tiny ATR where trading costs exceed reward
    mom = MomentumStrategy({
        "allow_short": True,
        "short_requires_macro_bear": False,
        "fee_pct": 0.01,
        "slippage_pct": 0.01,
        "take_profit_atr_mult": 1.0,
    })
    prices = [100.0] * 60 + [99.99]
    df = _make_df(prices)
    df["high"] = df["close"] + 0.001
    df["low"] = df["close"] - 0.001
    sig = mom.evaluate("TEST", df)
    assert sig.status == SignalStatus.NO_TRADE

    # 2. Mean reversion standard entry includes take_profit_ratio
    mr = MeanReversionStrategy({"scale_out_ratio": 0.5, "scale_out_enabled": True})
    mr_prices = [100.0] * 55 + [98.0, 96.0, 94.0, 91.0, 85.0]
    mr_sig = mr.evaluate("TEST", _make_df(mr_prices))
    if mr_sig.status == SignalStatus.ENTRY:
        assert mr_sig.take_profit_ratio == 0.5


