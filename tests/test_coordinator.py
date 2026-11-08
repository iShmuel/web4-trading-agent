"""Unit tests for PortfolioCoordinator: signal reconciliation, conflict resolution, and allocation."""

from datetime import datetime, timezone
import pytest
import pandas as pd

from core.signals import TradingSignal, SignalDirection, SignalStatus, create_no_trade_signal
from core.coordinator import PortfolioCoordinator
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine


def make_trending_df(n: int = 60, start: float = 100.0, step: float = 0.5) -> pd.DataFrame:
    idx = pd.date_range("2026-10-01", periods=n, freq="1h", tz="UTC")
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "open": [c - 0.1 for c in closes],
            "high": [c + 0.2 for c in closes],
            "low": [c - 0.2 for c in closes],
            "close": closes,
            "volume": [1000.0] * n,
        },
        index=idx,
    )


def test_coordinator_conflict_resolution_opposite_directions(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_port.json")
    account = PaperAccount(10000.0)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {})

    now = datetime.now(timezone.utc)
    # Momentum proposes LONG
    mom_sig = TradingSignal(
        strategy_id="momentum",
        symbol="BTC/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=60000.0,
        signal_strength=0.8,
    )
    # Mean Reversion proposes SHORT
    mr_sig = TradingSignal(
        strategy_id="mean_reversion",
        symbol="BTC/USDT",
        timestamp=now,
        direction=SignalDirection.SHORT,
        status=SignalStatus.ENTRY,
        reference_price=60000.0,
        signal_strength=0.7,
    )

    chosen, reason = coordinator.reconcile_signals("BTC/USDT", mom_sig, mr_sig)
    # Must reject both due to conflicting market state
    assert chosen is None
    assert "Conflict" in reason


def test_coordinator_reconcile_same_direction_picks_strongest(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_port.json")
    account = PaperAccount(10000.0)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {})

    now = datetime.now(timezone.utc)
    mom_sig = TradingSignal(
        strategy_id="momentum",
        symbol="ETH/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=3000.0,
        signal_strength=0.5,
    )
    mr_sig = TradingSignal(
        strategy_id="mean_reversion",
        symbol="ETH/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=3000.0,
        signal_strength=0.9,  # higher strength
    )

    chosen, reason = coordinator.reconcile_signals("ETH/USDT", mom_sig, mr_sig)
    assert chosen is not None
    assert chosen.strategy_id == "mean_reversion"
    assert "selected MeanReversion" in reason


def test_coordinator_cross_strategy_position_safety(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_port.json")
    account = PaperAccount(10000.0)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {"atr_multiplier": 2.5})

    # Strategy A (Momentum) opened LONG position
    account.open_position(
        symbol="SOL/USDT",
        entry_price=100.0,
        units=5.0,
        stop_loss=90.0,
        dollar_risk=50.0,
        side="LONG",
        strategy_id="momentum",
    )

    # Now Strategy B tries to validate a SHORT entry proposal on the same symbol
    verdict = risk.validate_proposed_entry(
        symbol="SOL/USDT",
        side="SHORT",
        strategy_id="mean_reversion",
        entry_price=100.0,
        suggested_stop=110.0,
        atr=5.0,
        atr_multiplier=2.0,
        equity=account.get_total_equity(),
        cash=account.cash,
        active_positions=account.positions,
    )

    assert verdict.approved is False
    assert "conflict" in verdict.reason.lower()


def test_coordinator_macro_bear_gate(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_port.json")
    account = PaperAccount(10000.0)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {"enable_regime_filter": True})

    now = datetime.now(timezone.utc)
    # Price is $50,000, but Macro 200 EMA is $60,000 (Bear Regime)
    mom_sig = TradingSignal(
        strategy_id="momentum",
        symbol="BTC/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=50000.0,
        indicators={"macro_ema": 60000.0, "adx": 30.0},
    )
    mr_sig = TradingSignal(
        strategy_id="mean_reversion",
        symbol="BTC/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.NO_TRADE,
        reference_price=50000.0,
    )

    chosen, reason = coordinator.reconcile_signals("BTC/USDT", mom_sig, mr_sig)
    assert chosen is None
    assert "Blocked by Macro Bear Gate" in reason


def test_coordinator_chop_gate(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_port.json")
    account = PaperAccount(10000.0)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {"enable_regime_filter": True, "adx_chop_threshold": 20.0})

    now = datetime.now(timezone.utc)
    # ADX is 15.0 (Choppy sideways range)
    mom_sig = TradingSignal(
        strategy_id="momentum",
        symbol="SOL/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=150.0,
        indicators={"macro_ema": 140.0, "adx": 15.0},
    )
    mr_sig = TradingSignal(
        strategy_id="mean_reversion",
        symbol="SOL/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=150.0,
        indicators={"adx": 15.0},
    )

    chosen, reason = coordinator.reconcile_signals("SOL/USDT", mom_sig, mr_sig)
    # Breakout is blocked, Mean Reversion is approved!
    assert chosen is not None
    assert chosen.strategy_id == "mean_reversion"


def test_coordinator_strong_trend_gate(tmp_path, monkeypatch):
    monkeypatch.setattr("core.paper_account.PORTFOLIO_FILE", tmp_path / "test_port.json")
    account = PaperAccount(10000.0)
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {"enable_regime_filter": True, "adx_trend_threshold": 25.0})

    now = datetime.now(timezone.utc)
    # ADX is 35.0 (Strong trend expansion)
    mom_sig = TradingSignal(
        strategy_id="momentum",
        symbol="NEAR/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=5.0,
        indicators={"macro_ema": 4.0, "adx": 35.0},
    )
    mr_sig = TradingSignal(
        strategy_id="mean_reversion",
        symbol="NEAR/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=5.0,
        indicators={"adx": 35.0},
    )

    chosen, reason = coordinator.reconcile_signals("NEAR/USDT", mom_sig, mr_sig)
    # Counter-trend MR suppressed, Momentum selected!
    assert chosen is not None
    assert chosen.strategy_id == "momentum"


def test_coordinator_breakeven_lock_on_partial_exit(tmp_path):
    account = PaperAccount(10000.0, portfolio_file=tmp_path / "be_port.json")
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {"enable_breakeven_lock": True})

    # Open a Mean-Reversion position on ETH/USDT at $2,250 with stop down at $2,200
    account.open_position(
        symbol="ETH/USDT",
        entry_price=2250.0,
        units=1.0,
        stop_loss=2200.0,
        dollar_risk=50.0,
        max_concurrent_positions=3,
        side="LONG",
        strategy_id="mean_reversion",
    )

    now = datetime.now(timezone.utc)
    _, _, mr_engine = coordinator._engines_for("ETH/USDT")
    mr_engine.evaluate = lambda sym, df, existing_position=None: TradingSignal(
        strategy_id="mean_reversion",
        symbol=sym,
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.PARTIAL_EXIT,
        reference_price=2290.0,
        atr=20.0,
        take_profit_ratio=0.5,
        exit_reason="Halfway Scale-Out Take-Profit (50% Banked)",
    )

    df = make_trending_df(n=60, start=2250.0, step=0.7)
    decision = coordinator.process_symbol("ETH/USDT", df, {})
    assert decision.action == "PARTIAL_EXIT"
    rem_pos = account.get_position("ETH/USDT")
    assert rem_pos is not None
    assert rem_pos["units"] == 0.5
    # Stop must be locked above entry price ($2,250 * 1.0015 = $2,253.375) instead of staying at $2,200!
    assert rem_pos["trailing_stop"] >= 2250.0 * 1.0015


def test_coordinator_crypto_crash_contagion_cooldown(tmp_path):
    account = PaperAccount(10000.0, portfolio_file=tmp_path / "cd_port.json")
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(
        account,
        risk,
        {"enable_crash_cooldown": True, "crypto_crash_cooldown_hours": 3.0, "enable_funding_radar": False},
    )

    # Simulate a recent crypto stop-loss 30 minutes ago
    df = make_trending_df(n=60, start=100.0, step=0.5)
    last_bar_ts = df.index[-1]
    coordinator._last_crypto_stop_time = last_bar_ts - pd.Timedelta(minutes=30)

    now = datetime.now(timezone.utc)
    _, mom_engine, mr_engine = coordinator._engines_for("SOL/USDT")
    mom_engine.evaluate = lambda sym, d, existing_position=None: create_no_trade_signal("momentum", sym, 100.0, "no")
    mr_engine.evaluate = lambda sym, d, existing_position=None: TradingSignal(
        strategy_id="mean_reversion",
        symbol=sym,
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=100.0,
        atr=2.0,
        suggested_stop=96.0,
        suggested_take_profit=105.0,
        indicators={"adx": 15.0},
    )

    decision = coordinator.process_symbol("SOL/USDT", df, {})
    assert decision.action == "REJECTED"
    assert "Contagion Cooldown" in decision.reason
    assert account.get_position("SOL/USDT") is None


def test_coordinator_surge_volume_conviction_boost(tmp_path, monkeypatch):
    """Verify >= 1.5x volume surge boosts position sizing by 1.2x while respecting [1%, 5%] risk bounds."""
    from core.polymarket_radar import MacroEventRisk
    from core.fred_radar import MacroRegimeReport

    monkeypatch.setattr(
        "core.coordinator.PolymarketRadar.evaluate",
        lambda self, force_refresh=False: MacroEventRisk(risk_level="LOW", recommended_risk_multiplier=1.0),
    )
    monkeypatch.setattr(
        "core.coordinator.FredMacroRadar.evaluate",
        lambda self, force_refresh=False: MacroRegimeReport(
            regime="STABLE",
            vix_level=15.0,
            risk_scale_factor=1.0,
            allow_long_breakouts=True,
            reason="Stable",
        ),
    )

    now = datetime.now(timezone.utc)
    entry_sig = TradingSignal(
        strategy_id="momentum",
        symbol="SOL/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=100.0,
        atr=5.0,
        suggested_stop=88.75,
        suggested_take_profit=120.0,
        indicators={"adx": 28.0, "macro_ema": 90.0},
    )

    # 1. Normal volume (1.0x SMA20)
    acc_norm = PaperAccount(10000.0, portfolio_file=tmp_path / "norm_port.json")
    risk_norm = RiskEngine(default_risk_pct=0.02, symbol_risk_weights={"SOL/USDT": 0.025})
    coord_norm = PortfolioCoordinator(
        acc_norm,
        risk_norm,
        {"enable_volume_boost": True, "volume_surge_mult": 1.5, "volume_boost_factor": 1.2, "enable_funding_radar": False},
    )
    _, mom_n, mr_n = coord_norm._engines_for("SOL/USDT")
    mom_n.evaluate = lambda sym, d, existing_position=None: entry_sig
    mr_n.evaluate = lambda sym, d, existing_position=None: create_no_trade_signal("mean_reversion", sym, 100.0, "no")

    df_norm = make_trending_df(n=60, start=90.0, step=0.2)
    df_norm["volume"] = 1000.0
    dec_norm = coord_norm.process_symbol("SOL/USDT", df_norm, {})
    assert dec_norm.action == "ENTER_LONG"

    # 2. Surge volume (2.0x SMA20 on latest bar)
    acc_surge = PaperAccount(10000.0, portfolio_file=tmp_path / "surge_port.json")
    risk_surge = RiskEngine(default_risk_pct=0.02, symbol_risk_weights={"SOL/USDT": 0.025})
    coord_surge = PortfolioCoordinator(
        acc_surge,
        risk_surge,
        {"enable_volume_boost": True, "volume_surge_mult": 1.5, "volume_boost_factor": 1.2, "enable_funding_radar": False},
    )
    _, mom_s, mr_s = coord_surge._engines_for("SOL/USDT")
    mom_s.evaluate = lambda sym, d, existing_position=None: entry_sig
    mr_s.evaluate = lambda sym, d, existing_position=None: create_no_trade_signal("mean_reversion", sym, 100.0, "no")

    df_surge = make_trending_df(n=60, start=90.0, step=0.2)
    df_surge["volume"] = 1000.0
    df_surge.iloc[-1, df_surge.columns.get_loc("volume")] = 2000.0
    dec_surge = coord_surge.process_symbol("SOL/USDT", df_surge, {})
    assert dec_surge.action == "ENTER_LONG"
    assert abs(dec_surge.units / dec_norm.units - 1.2) < 1e-4




def test_asset_pruning_blacklist_defense(tmp_path):
    """Verify that blacklisted/pruned assets are strictly rejected on entry but allowed to exit."""
    account = PaperAccount(10000.0, portfolio_file=tmp_path / "prune_port.json")
    risk_engine = RiskEngine()
    coordinator = PortfolioCoordinator(
        account,
        risk_engine,
        config={"blacklisted_symbols": ["ETH/USDT", "AVAX/USDT"], "enable_funding_radar": False},
    )

    df = make_trending_df(n=50, start=2000.0, step=10.0)

    # 1. Fresh entry on blacklisted symbol must be REJECTED immediately
    decision = coordinator.process_symbol("ETH/USDT", df, {})
    assert decision.action == "REJECTED"
    assert "Asset Pruned: ETH/USDT is blacklisted" in decision.reason

    # 2. Existing position must still be allowed to exit gracefully
    account.open_position(
        symbol="ETH/USDT",
        entry_price=2500.0,
        units=1.0,
        stop_loss=2400.0,
        dollar_risk=50.0,
        side="LONG",
        strategy_id="momentum",
    )
    assert account.has_position("ETH/USDT")

    # Mock an exit signal
    exit_sig = TradingSignal(
        status=SignalStatus.EXIT,
        strategy_id="momentum",
        symbol="ETH/USDT",
        timestamp=datetime.now(timezone.utc),
        direction=SignalDirection.FLAT,
        signal_strength=1.0,
        reference_price=2300.0,
        exit_reason="Stop Loss Hit",
    )
    _, mom_engine, _ = coordinator._engines_for("ETH/USDT")
    mom_engine.evaluate = lambda sym, d, existing_position=None: exit_sig

    exit_decision = coordinator.process_symbol("ETH/USDT", df, {})
    assert exit_decision.action == "EXIT"
    assert not account.has_position("ETH/USDT")


def test_coordinator_reload_config_hot_reloads_and_enforces_risk_bounds(tmp_path):
    """Verify reload_config refreshes strategy engines, clears symbol cache, and clamps risk bounds."""
    account = PaperAccount(10000.0, portfolio_file=tmp_path / "reload_port.json")
    risk_engine = RiskEngine(default_risk_pct=0.02, max_open_positions=3)
    initial_cfg = {
        "breakout_lookback": 20,
        "account_risk_pct": 0.02,
        "max_concurrent_positions": 3,
        "asset_profiles": {
            "crypto_fast": {
                "symbols": ["SOL/USDT"],
                "breakout_lookback": 16,
            }
        },
    }
    coordinator = PortfolioCoordinator(account, risk_engine, initial_cfg)
    assert coordinator._engines_for("SOL/USDT")[1].breakout_lookback == 16
    assert coordinator._engines_for("NVDA")[1].breakout_lookback == 20

    # Hot-reload with updated lookbacks and out-of-bounds risk weight (0.10 -> clamped to 0.05)
    updated_cfg = {
        "breakout_lookback": 32,
        "account_risk_pct": 0.03,
        "max_concurrent_positions": 5,
        "symbol_risk_weights": {"SOL/USDT": 0.10, "NVDA": 0.005},
        "symbol_exposure_caps": {"SOL/USDT": 0.40},
        "asset_profiles": {
            "crypto_fast": {
                "symbols": ["SOL/USDT"],
                "breakout_lookback": 24,
            }
        },
    }
    coordinator.reload_config(updated_cfg)

    assert coordinator.config["breakout_lookback"] == 32
    assert coordinator._engines_for("SOL/USDT")[1].breakout_lookback == 24
    assert coordinator._engines_for("NVDA")[1].breakout_lookback == 32
    assert risk_engine.risk_pct == 0.03
    assert risk_engine.max_open_positions == 5
    # Must strictly clamp symbol risk weights to [0.01, 0.05]
    assert risk_engine.symbol_risk_weights["SOL/USDT"] == 0.05
    assert risk_engine.symbol_risk_weights["NVDA"] == 0.01
    assert risk_engine.symbol_exposure_caps["SOL/USDT"] == 0.40


def test_funding_regime_gate_blocks_longs_from_config_and_process_symbol(tmp_path, monkeypatch):
    """Verify funding regime gate blocks LONG entries across all strategies via config and full process_symbol."""
    account = PaperAccount(10000.0, portfolio_file=tmp_path / "fund_port.json")
    risk = RiskEngine()
    # 1. Direct config funding_regime == "OVERHEATED_LONGS"
    coordinator = PortfolioCoordinator(
        account,
        risk,
        {"enable_regime_filter": True, "funding_regime": "OVERHEATED_LONGS", "funding_rate_pct": 0.085},
    )
    now = datetime.now(timezone.utc)
    mom_sig = TradingSignal(
        strategy_id="momentum",
        symbol="BTC/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=70000.0,
        indicators={"macro_ema": 65000.0, "adx": 28.0},
    )
    mr_sig = TradingSignal(
        strategy_id="mean_reversion",
        symbol="BTC/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=70000.0,
        indicators={"adx": 22.0},
    )
    chosen, reason = coordinator.reconcile_signals("BTC/USDT", mom_sig, mr_sig)
    assert chosen is None
    assert "Overheated Funding Rate" in reason

    # 2. Full process_symbol execution with live funding radar returning overheated funding (> 0.0005)
    monkeypatch.setattr("core.coordinator.fetch_funding_rate", lambda sym: 0.0009)
    coord_live = PortfolioCoordinator(
        account,
        risk,
        {"enable_regime_filter": True, "enable_funding_radar": True},
    )
    _, mom_eng, mr_eng = coord_live._engines_for("BTC/USDT")
    mom_eng.evaluate = lambda sym, d, existing_position=None: TradingSignal(
        strategy_id="momentum",
        symbol=sym,
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=70000.0,
        atr=500.0,
        suggested_stop=68500.0,
        indicators={"macro_ema": 65000.0, "adx": 28.0},
    )
    mr_eng.evaluate = lambda sym, d, existing_position=None: create_no_trade_signal("mean_reversion", sym, 70000.0, "no")

    df = make_trending_df(n=60, start=68000.0, step=35.0)
    dec = coord_live.process_symbol("BTC/USDT", df, {})
    assert dec.action == "REJECTED"
    assert "Overheated Funding Rate" in dec.reason
    assert dec.indicators.get("funding_regime") == "OVERHEATED_LONGS"
    assert account.get_position("BTC/USDT") is None


def test_funding_regime_missing_or_invalid_data_fails_safely(tmp_path):
    """Verify missing (when required) or malformed/NaN funding data fails closed and blocks entries."""
    account = PaperAccount(10000.0, portfolio_file=tmp_path / "fund_fail_port.json")
    risk = RiskEngine()
    now = datetime.now(timezone.utc)
    entry_sig = TradingSignal(
        strategy_id="momentum",
        symbol="SOL/USDT",
        timestamp=now,
        direction=SignalDirection.LONG,
        status=SignalStatus.ENTRY,
        reference_price=150.0,
    )
    no_sig = create_no_trade_signal("mean_reversion", "SOL/USDT", 150.0, "none")

    # Case A: Invalid funding_regime string -> fail closed
    coord_bad_regime = PortfolioCoordinator(account, risk, {"funding_regime": "CORRUPTED_REGIME"})
    chosen_a, reason_a = coord_bad_regime.reconcile_signals("SOL/USDT", entry_sig, no_sig)
    assert chosen_a is None
    assert "Blocked by Funding Gate" in reason_a

    # Case B: NaN funding_rate_pct -> fail closed
    coord_nan_rate = PortfolioCoordinator(account, risk, {"funding_rate_pct": float("nan")})
    chosen_b, reason_b = coord_nan_rate.reconcile_signals("SOL/USDT", entry_sig, no_sig)
    assert chosen_b is None
    assert "Blocked by Funding Gate" in reason_b

    # Case C: Missing funding data when require_funding_data=True -> fail closed
    coord_req = PortfolioCoordinator(account, risk, {"require_funding_data": True})
    chosen_c, reason_c = coord_req.reconcile_signals("SOL/USDT", entry_sig, no_sig)
    assert chosen_c is None
    assert "Missing required funding data" in reason_c


def test_persistent_drawdown_circuit_breaker_survives_restart(tmp_path):
    """Verify peak_equity and tripped max-drawdown circuit breaker persist across PaperAccount & RiskEngine restarts."""
    port_file = tmp_path / "persistent_dd_portfolio.json"
    account1 = PaperAccount(10000.0, portfolio_file=port_file)
    risk1 = RiskEngine(max_drawdown_limit_pct=0.15, account=account1)

    # 1. Portfolio grows to $12,000 high-water mark
    healthy_peak, _ = risk1.check_circuit_breakers(12000.0)
    assert healthy_peak is True
    assert risk1.peak_equity == 12000.0

    # Simulate process restart #1: reload PaperAccount and fresh RiskEngine from disk
    account2 = PaperAccount(10000.0, portfolio_file=port_file)
    risk2 = RiskEngine(max_drawdown_limit_pct=0.15, account=account2)
    assert account2.state["peak_equity"] == 12000.0
    assert risk2.peak_equity == 12000.0
    assert risk2.is_halted is False

    # 2. Equity drops to $10,000 (16.67% drawdown from $12,000 peak, even though equal to initial $10,000!)
    healthy_drop, drop_reason = risk2.check_circuit_breakers(10000.0)
    assert healthy_drop is False
    assert "Max drawdown circuit breaker triggered" in drop_reason
    assert risk2.is_halted is True
    assert account2.state["circuit_breaker_status"] == "TRIPPED"
    assert account2.state["circuit_breaker_tripped"] is True
    assert account2.state["circuit_breaker_triggered_at"] is not None

    # 3. Simulate process restart #2 after crash/reboot: breaker MUST remain tripped!
    account3 = PaperAccount(10000.0, portfolio_file=port_file)
    risk3 = RiskEngine(max_drawdown_limit_pct=0.15)
    coord3 = PortfolioCoordinator(account3, risk3, {"enable_funding_radar": False})
    assert risk3.is_halted is True
    assert risk3.peak_equity == 12000.0

    # Verify new entries are blocked while breaker is active
    verdict = risk3.validate_proposed_entry(
        symbol="BTC/USDT",
        side="LONG",
        strategy_id="momentum",
        entry_price=70000.0,
        suggested_stop=68000.0,
        atr=800.0,
        atr_multiplier=2.5,
        equity=10000.0,
        cash=10000.0,
        active_positions={},
    )
    assert verdict.approved is False
    assert "Trading halted" in verdict.reason

    # Unauthorized reset attempt must be rejected
    assert risk3.reset_kill_switch(authorized=False) is False
    assert risk3.is_halted is True

    # Authorized reset clears breaker and persists to disk
    assert risk3.reset_kill_switch(authorized=True, reset_peak_to_current=10000.0) is True
    assert risk3.is_halted is False
    account4 = PaperAccount(10000.0, portfolio_file=port_file)
    assert account4.state["circuit_breaker_status"] == "OK"
    assert account4.state["circuit_breaker_tripped"] is False


def test_dynamic_multi_strategy_registration_beyond_two_strategies(tmp_path):
    """Verify coordinator supports >2 registered strategies and reconciles N custom strategy plugins."""
    account = PaperAccount(10000.0, portfolio_file=tmp_path / "multistrat_port.json")
    risk = RiskEngine()
    coordinator = PortfolioCoordinator(account, risk, {"enable_funding_radar": False})

    # Verify all 4 built-in strategies are registered by default
    reg = coordinator.get_registered_strategies()
    assert set(reg.keys()) >= {"momentum", "mean_reversion", "stat_arb_pairs", "basis_yield"}

    # Register a 5th custom strategy plugin dynamically
    now = datetime.now(timezone.utc)

    class VolatilityBreakoutPlugin:
        STRATEGY_ID = "vol_breakout_v1"
        STRATEGY_NAME = "Volatility Compression Breakout"

        def evaluate(self, symbol: str, df: pd.DataFrame, existing_position=None) -> TradingSignal:
            return TradingSignal(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                timestamp=now,
                direction=SignalDirection.LONG,
                status=SignalStatus.ENTRY,
                reference_price=float(df.iloc[-1]["close"]),
                signal_strength=0.95,
                atr=2.0,
                suggested_stop=float(df.iloc[-1]["close"]) - 4.0,
                suggested_take_profit=float(df.iloc[-1]["close"]) + 8.0,
                indicators={"adx": 23.0},
            )

    coordinator.register_strategy("vol_breakout_v1", VolatilityBreakoutPlugin(), enabled=True)
    reg_after = coordinator.get_registered_strategies()
    assert len(reg_after) == 5
    assert "vol_breakout_v1" in reg_after

    # Disable momentum and mean_reversion, leaving only the 5th custom strategy active for directional signals
    coordinator.set_strategy_enabled("momentum", False)
    coordinator.set_strategy_enabled("mean_reversion", False)

    df = make_trending_df(n=60, start=100.0, step=0.5)
    dec = coordinator.process_symbol("AAPL", df, {})
    assert dec.strategy_id == "vol_breakout_v1"
    assert dec.action in ("ENTER_LONG", "REJECTED")  # Evaluated through the 5th strategy


