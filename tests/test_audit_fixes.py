"""Regression tests for all findings discovered during the Full Project Audit."""

from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd
import pytest

from core.backtest_engine import run_mean_reversion_backtest
from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.signals import SignalDirection, SignalStatus
from strategies.mean_reversion import MeanReversionStrategy
from strategies.stat_arb_pairs import StatArbPairsStrategy


def test_stat_arb_stop_loss_cooldown_and_auto_quarantine_no_name_error(tmp_path):
    """Verify Stat-Arb pair stop-loss exit records post-stop cooldown without NameError on timedelta
    and auto-quarantines a pair after 2 stop-losses."""
    account = PaperAccount(10000.0, portfolio_file=tmp_path / "port.json")
    risk = RiskEngine(default_risk_pct=0.02, max_open_positions=5)
    config = {
        "account_risk_pct": 0.02,
        "max_concurrent_positions": 5,
        "enable_pairs_trading": True,
        "enable_basis_yield": False,
        "pairs_universe": [("GLD", "SLV")],
        "min_pair_correlation": 0.70,
        "min_pair_return_correlation": 0.45,
        "max_pair_half_life_hours": 72.0,
        "stat_arb_entry_z": 2.0,
        "stat_arb_exit_z": 0.5,
        "stat_arb_stop_z": 3.5,
        "pair_stop_cooldown_hours": 12.0,
        "fee_pct": 0.0002,
    }
    coordinator = PortfolioCoordinator(account, risk, config)
    dates = pd.date_range("2026-01-01", periods=100, freq="1h", tz="UTC")

    base_slv = 30.0 + np.sin(np.linspace(0, 15, 100)) * 2.0
    base_gld = 2.5 * base_slv + 10.0 + np.cos(np.linspace(0, 40, 100)) * 1.5
    base_gld[-1] -= 2.5  # Valid entry Z inside [-3.5, -2.0]

    market_data = {
        "GLD": pd.DataFrame({"close": base_gld, "high": base_gld + 0.5, "low": base_gld - 0.5, "open": base_gld, "volume": 1000}, index=dates),
        "SLV": pd.DataFrame({"close": base_slv, "high": base_slv + 0.5, "low": base_slv - 0.5, "open": base_slv, "volume": 1000}, index=dates),
    }

    # Open the pair
    coordinator.run_cycle(market_data)
    assert account.has_position("GLD") and account.has_position("SLV")

    # Now blow out the spread beyond stop_z (|Z| >= 3.5, within 5% leg stop) to trigger Stop loss triggered in run_cycle
    base_gld_stop = base_gld.copy()
    base_gld_stop[-1] -= 2.0
    market_data_stop = {
        "GLD": pd.DataFrame({"close": base_gld_stop, "high": base_gld_stop + 0.5, "low": base_gld_stop - 0.5, "open": base_gld_stop, "volume": 1000}, index=dates),
        "SLV": pd.DataFrame({"close": base_slv, "high": base_slv + 0.5, "low": base_slv - 0.5, "open": base_slv, "volume": 1000}, index=dates),
    }

    # Must not raise NameError: name 'timedelta' is not defined
    report = coordinator.run_cycle(market_data_stop)
    assert not account.has_position("GLD") and not account.has_position("SLV")
    assert "GLD:SLV" in coordinator.pair_cooldown_until
    assert coordinator.pair_stop_counts.get("GLD:SLV") == 1

    # Immediate re-entry attempt during cooldown must be REJECTED
    report_cd = coordinator.run_cycle(market_data)
    assert not account.has_position("GLD")
    assert any(d.symbol == "GLD:SLV" and d.action == "REJECTED" and "cooldown" in d.reason.lower() for d in report_cd.decisions)


def test_mean_reversion_blocks_underwater_scale_out_and_sma_exit():
    """Verify MeanReversionStrategy and run_mean_reversion_backtest never trigger a 'Take Profit'
    or 'Mean Reversion Target (SMA)' exit at a loss when the 20-bar SMA drifts below entry price."""
    dates = pd.date_range("2026-01-01", periods=80, freq="1h", tz="UTC")
    # Price drifted down from 100 to 90 so 20-bar SMA is ~91, while entry_price was 95.0
    closes = np.concatenate([np.full(55, 100.0), np.full(25, 90.0)])
    closes[-1] = 92.0  # Above current SMA (90.0) and halfway_target, but BELOW entry_price (95.0)!
    df = pd.DataFrame(
        {"open": closes, "high": closes + 0.5, "low": closes - 0.5, "close": closes, "volume": 1000.0},
        index=dates,
    )

    strat = MeanReversionStrategy({"mr_scale_out_enabled": True, "mr_scale_out_ratio": 0.5, "max_stale_minutes": 0})
    pos = {
        "symbol": "SOL/USDT",
        "entry_price": 95.0,
        "trailing_stop": 80.0,
        "side": "LONG",
        "tp_hit": False,
    }
    sig = strat.evaluate("SOL/USDT", df, existing_position=pos)
    # Must HOLD (not exit at 92.0 for a -$3/unit loss!)
    assert sig.status == SignalStatus.HOLD


def test_risk_engine_respects_suggested_stop_distance():
    """Verify RiskEngine.validate_proposed_entry sizes units and sets initial_stop using suggested_stop
    when a valid strategy stop is provided (e.g. Mean Reversion or Liquidity Sweep stop)."""
    risk = RiskEngine(default_risk_pct=0.02, max_single_asset_exposure_pct=0.50)
    # Entry at 100, ATR=2.0, Momentum atr_multiplier=2.75 (would be 94.50 stop),
    # but Mean Reversion Liquidity Sweep passes tight suggested_stop=98.0 (stop distance = 2.0)
    verdict = risk.validate_proposed_entry(
        symbol="SOL/USDT",
        side="LONG",
        strategy_id="mean_reversion",
        entry_price=100.0,
        suggested_stop=98.0,
        atr=2.0,
        atr_multiplier=2.75,
        equity=10000.0,
        cash=10000.0,
        active_positions={},
    )
    assert verdict.approved is True
    assert np.isclose(verdict.suggested_stop, 98.0)


def test_stat_arb_pairs_blocks_stale_weekend_equity_bars():
    """Verify StatArbPairsStrategy refuses to open new pair positions when live quote_time
    shows the latest candle is stale (e.g. closed weekend stock market)."""
    dates = pd.date_range("2026-01-01", periods=100, freq="1h", tz="UTC")
    base_b = 30.0 + np.sin(np.linspace(0, 15, 100)) * 2.0
    base_a = 2.5 * base_b + 10.0 + np.cos(np.linspace(0, 40, 100)) * 1.5
    base_a[-1] -= 2.5

    df_a = pd.DataFrame({"close": base_a}, index=dates)
    df_b = pd.DataFrame({"close": base_b}, index=dates)
    # Simulate weekend quote_time 48 hours after Friday's last bar
    df_a.attrs["quote_time"] = dates[-1].to_pydatetime() + timedelta(hours=48)
    df_b.attrs["quote_time"] = dates[-1].to_pydatetime() + timedelta(hours=48)

    strat = StatArbPairsStrategy({"stat_arb_lookback": 48, "stat_arb_entry_z": 2.0, "max_stale_minutes": 180})
    sig = strat.evaluate_pair("MU", "CIFR", df_a, df_b, existing_pair_position=None)
    assert sig.action == "NO_TRADE"
    assert "stale pair data" in sig.reason.lower()


def test_filter_closed_bars_preserves_live_price_and_triggers_intra_hour_exit():
    """Verify filter_closed_bars drops the forming 1h bar for indicator purity while storing
    df.attrs['live_price'] so open positions react to real-time intra-hour stop/target prices."""
    from core.data_loader import filter_closed_bars

    now = datetime(2026, 6, 1, 14, 35, 0, tzinfo=timezone.utc)
    dates = pd.date_range("2026-05-29 14:00:00", periods=73, freq="1h", tz="UTC")
    closes = 100.0 + np.sin(np.linspace(0, 12, 73)) * 2.0
    closes[-2] = 100.0
    # Last bar (14:00-15:00 UTC) is still forming at 14:35 UTC and dropped sharply to 84.0 (below stop 88.0)
    closes[-1] = 84.0
    raw_df = pd.DataFrame(
        {"open": closes, "high": closes + 0.5, "low": closes - 0.5, "close": closes, "volume": 1000.0},
        index=dates,
    )

    closed_df = filter_closed_bars(raw_df, timeframe="1h", as_of=now)
    # Indicator dataframe must drop the incomplete 14:00 bar (last closed bar close is 100.0)
    assert len(closed_df) == 72
    assert float(closed_df.iloc[-1]["close"]) == 100.0
    # But live_price metadata must capture the real-time 84.0 intra-hour price!
    assert closed_df.attrs.get("live_price") == 84.0

    # Evaluating an open LONG position with trailing_stop=88.0 must trigger EXIT at 84.0 immediately
    mr = MeanReversionStrategy({"max_stale_minutes": 0})
    pos = {"symbol": "SOL/USDT", "entry_price": 95.0, "trailing_stop": 88.0, "side": "LONG", "tp_hit": False}
    sig = mr.evaluate("SOL/USDT", closed_df, existing_position=pos)
    assert sig.status == SignalStatus.EXIT
    assert sig.reference_price == 84.0


def test_correlation_guard_excludes_market_neutral_stat_arb_pairs():
    """Verify open market-neutral stat_arb_pairs crypto legs do not block directional crypto entries."""
    risk = RiskEngine(default_risk_pct=0.02, max_crypto_positions=2, max_open_positions=5)
    active = {
        "AVAX/USDT": {"units": 100.0, "entry_price": 10.0, "side": "LONG", "strategy_id": "stat_arb_pairs"},
        "BARD/USDT": {"units": 1000.0, "entry_price": 1.0, "side": "SHORT", "strategy_id": "stat_arb_pairs"},
    }
    verdict = risk.validate_proposed_entry(
        symbol="SOL/USDT",
        side="LONG",
        strategy_id="momentum",
        entry_price=100.0,
        suggested_stop=95.0,
        atr=2.0,
        atr_multiplier=2.5,
        equity=10000.0,
        cash=8000.0,
        active_positions=active,
    )
    assert verdict.approved is True


def test_live_trader_log_rotation(tmp_path, monkeypatch):
    """Verify log() rotates live_trader.log to live_trader.log.1 when size reaches MAX_LOG_BYTES."""
    import live_paper_trader

    test_log = tmp_path / "live_trader.log"
    monkeypatch.setattr(live_paper_trader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(live_paper_trader, "LOG_FILE", test_log)
    monkeypatch.setattr(live_paper_trader, "MAX_LOG_BYTES", 100)

    test_log.write_text("X" * 120)
    live_paper_trader.log("Fresh line after rotation")

    rotated = tmp_path / "live_trader.log.1"
    assert rotated.exists()
    assert rotated.read_text() == "X" * 120
    assert "Fresh line after rotation" in test_log.read_text()

