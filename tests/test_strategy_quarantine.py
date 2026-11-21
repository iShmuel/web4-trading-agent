"""Tests for Automatic Strategy-Level OOS Quarantine & Auto-Rehabilitation Gate."""

from agent.strategy_quarantine import (
    evaluate_strategy_quarantine_decisions,
    load_quarantine_state,
    save_quarantine_state,
)


def test_auto_quarantine_and_min_active_floor():
    cfg = {
        "enable_momentum": True,
        "enable_mean_reversion": True,
        "enable_volatility_squeeze": True,
        "enable_vwap_reversion": False,
    }
    scores = {
        # Losing active strategy -> should be QUARANTINED
        "momentum": (15, -420.0, 0.72, 14.0),
        # Healthy active strategies -> KEEP_ON
        "mean_reversion": (12, 310.0, 1.55, 5.2),
        "volatility_squeeze": (18, 520.0, 1.68, 6.1),
        # Still failing quarantined strategy -> KEEP_OFF
        "vwap_reversion": (10, -180.0, 0.80, 16.0),
    }
    state = {"rehab_streaks": {}}
    reports, updates = evaluate_strategy_quarantine_decisions(scores, cfg, state, min_active_strategies=2)
    by_id = {r.strategy_id: r for r in reports}

    assert by_id["momentum"].action == "QUARANTINE"
    assert updates == {"enable_momentum": False}
    assert by_id["mean_reversion"].action == "KEEP_ON"
    assert by_id["volatility_squeeze"].action == "KEEP_ON"
    assert by_id["vwap_reversion"].action == "KEEP_OFF"


def test_auto_return_requires_two_consecutive_passing_oos_checks(tmp_path):
    cfg = {
        "enable_volatility_squeeze": True,
        "enable_mean_reversion": True,
        "enable_range_envelope": False,  # Currently quarantined
    }
    passing_scores = {
        "volatility_squeeze": (14, 400.0, 1.60, 5.0),
        "mean_reversion": (10, 220.0, 1.45, 4.0),
        "range_envelope": (12, 350.0, 1.48, 7.5),  # Passes PF >= 1.30 & DD <= 12%
    }
    state_file = tmp_path / "quarantine_state.json"
    state = load_quarantine_state(state_file)

    # Week 1: Progress 1/2 -> NOT re-enabled yet
    rep1, upd1 = evaluate_strategy_quarantine_decisions(passing_scores, cfg, state, rehab_consecutive_passes=2)
    by_id1 = {r.strategy_id: r for r in rep1}
    assert by_id1["range_envelope"].action == "REHAB_PROGRESS"
    assert by_id1["range_envelope"].consecutive_rehab_passes == 1
    assert "enable_range_envelope" not in upd1
    save_quarantine_state(state, state_file)

    # Week 2: Reload state, second consecutive pass -> AUTO-RETURN (REHABILITATE)!
    state2 = load_quarantine_state(state_file)
    rep2, upd2 = evaluate_strategy_quarantine_decisions(passing_scores, cfg, state2, rehab_consecutive_passes=2)
    by_id2 = {r.strategy_id: r for r in rep2}
    assert by_id2["range_envelope"].action == "REHABILITATE"
    assert upd2 == {"enable_range_envelope": True}
    assert state2["rehab_streaks"]["range_envelope"] == 0


def test_progressive_drawdown_throttle_halves_risk_at_10pct_dd():
    from core.risk_engine import RiskEngine

    risk = RiskEngine(
        default_risk_pct=0.025,
        max_single_asset_exposure_pct=0.90,
        dynamic_scaling=False,
        enable_drawdown_throttle=True,
        drawdown_throttle_pct=0.10,
        drawdown_throttle_mult=0.50,
    )
    risk._peak_equity = 10_000.0

    from datetime import datetime, timezone
    # At 5% DD -> full 1.0x risk (2.5%)
    assert risk.get_drawdown_risk_multiplier(9500.0) == 1.0
    v_normal = risk.validate_proposed_entry(
        symbol="AAPL", side="LONG", strategy_id="volatility_squeeze",
        entry_price=200.0, suggested_stop=190.0, atr=5.0, atr_multiplier=2.0,
        equity=9500.0, cash=9500.0, active_positions={},
        current_time=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert v_normal.approved
    assert abs((v_normal.dollar_risk / 9500.0) - 0.025) < 1e-4

    # Next day at 10.5% cumulative DD -> throttled 0.5x risk (1.25%, still inside 1%-5% bounds!)
    assert risk.get_drawdown_risk_multiplier(8950.0) == 0.5
    v_throttled = risk.validate_proposed_entry(
        symbol="AAPL", side="LONG", strategy_id="volatility_squeeze",
        entry_price=200.0, suggested_stop=190.0, atr=5.0, atr_multiplier=2.0,
        equity=8950.0, cash=8950.0, active_positions={},
        current_time=datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc),
    )
    assert v_throttled.approved
    assert abs((v_throttled.dollar_risk / 8950.0) - 0.0125) < 1e-4

    # Recovery back to 8% DD -> back to 1.0x
    assert risk.get_drawdown_risk_multiplier(9200.0) == 1.0
