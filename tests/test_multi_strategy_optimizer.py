"""Tests for the multi-strategy, multi-profile optimizer (Agent 11 v2)."""

import json

import numpy as np
import pandas as pd
import pytest

import agent.multi_strategy_optimizer as mso
from agent.governor import audit_invariants
from agent.validator import (
    evaluate_mean_reversion_parameters,
    evaluate_pairs_parameters,
    evaluate_parameters,
    validate_with_evaluator,
)
from core.backtest_engine import run_pairs_backtest


def _ohlcv(prices, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(prices), freq="1h", tz="UTC")
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame(
        {"open": p, "high": p * 1.004, "low": p * 0.996, "close": p, "volume": 1000.0}, index=idx
    )


def _random_walk(n=900, seed=0, drift=0.0):
    rng = np.random.default_rng(seed)
    return 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))


@pytest.fixture
def cfg():
    return {
        "watchlist": ["SOL/USDT", "LINK/USDT", "NVDA", "SMCI", "HIMS", "BAD"],
        "blacklisted_symbols": ["BAD"],
        "execution_mode": "paper",
        "account_risk_pct": 0.02,
        "timeframe": "1h",
        "use_asset_profiles": True,
        "asset_profiles": {
            "crypto_micro_swing": {"symbols": ["SOL/USDT", "LINK/USDT", "SUI/USDT"], "breakout_lookback": 24},
            "dead_profile": {"symbols": ["TAO/USDT"], "breakout_lookback": 24},
        },
        "pairs_universe": [["GLD", "SLV"], ["BTC/USDT", "ETH/USDT"]],
        "breakout_lookback": 48,
        "ema_trend_filter": 50,
        "atr_multiplier": 2.25,
        "bb_std": 1.9,
        "stat_arb_entry_z": 2.5,
    }


def test_rotation_covers_every_strategy_and_only_active_profiles(cfg):
    tasks = mso.build_optimization_tasks(cfg)
    labels = [mso.task_label(t) for t in tasks]

    assert "momentum:global" in labels
    assert "mean_reversion:global" in labels
    assert "stat_arb_pairs:global" in labels
    assert "basis_yield:global" in labels
    assert "momentum:crypto_micro_swing" in labels
    assert "mean_reversion:crypto_micro_swing" in labels
    # Profile without active watchlist symbols is skipped
    assert "momentum:dead_profile" not in labels
    assert "mean_reversion:dead_profile" not in labels
    # Blacklisted symbols are never used for tuning
    for t in tasks:
        assert "BAD" not in t.get("symbols", [])
    # Global momentum only uses symbols that have no asset profile
    glob = next(t for t in tasks if mso.task_label(t) == "momentum:global")
    assert set(glob["symbols"]) <= {"NVDA", "SMCI", "HIMS"}


def test_no_search_grid_mutates_risk():
    for grid in (mso.MOMENTUM_GRID, mso.MEAN_REVERSION_GRID, mso.PAIRS_GRID, mso.BASIS_YIELD_GRID):
        assert "account_risk_pct" not in grid


def test_all_grid_values_within_governor_bounds(cfg):
    for scope_grid in (mso.MOMENTUM_GRID, mso.MEAN_REVERSION_GRID, mso.PAIRS_GRID, mso.BASIS_YIELD_GRID):
        for combo in mso._grid_combos(scope_grid):
            cand = mso._apply(cfg, "global", combo)
            ok, errs = audit_invariants(cand)
            assert ok, errs


def test_profile_scope_only_changes_that_profile(cfg):
    cand = mso._apply(cfg, "crypto_micro_swing", {"breakout_lookback": 32})
    assert cand["asset_profiles"]["crypto_micro_swing"]["breakout_lookback"] == 32
    assert cand["breakout_lookback"] == 48  # global untouched
    assert cfg["asset_profiles"]["crypto_micro_swing"]["breakout_lookback"] == 24  # original untouched


def test_pairs_backtest_trades_on_mean_reverting_spread():
    rng = np.random.default_rng(1)
    base = _random_walk(900, seed=2)
    # B tracks A with a stationary, mean-reverting noise term
    noise = np.zeros(900)
    for i in range(1, 900):
        noise[i] = 0.8 * noise[i - 1] + rng.normal(0, 0.6)
    a = _ohlcv(base + noise)
    b = _ohlcv(base)

    res = run_pairs_backtest(a, b, lookback=48, entry_z=2.0, exit_z=0.5, stop_z=4.0)
    assert res.total_trades > 0
    assert res.total_fees_paid > 0
    assert len(res.equity_curve) > 1
    assert all(t.strategy_id == "stat_arb_pairs" for t in res.trades)

    # Cost gate can only reduce the number of trades
    gated = run_pairs_backtest(a, b, lookback=48, entry_z=2.0, exit_z=0.5, stop_z=4.0, min_edge_cost_ratio=50.0)
    assert gated.total_trades <= res.total_trades


def test_evaluators_use_live_config_keys():
    df = _ohlcv(_random_walk(600, seed=3))
    r1 = evaluate_mean_reversion_parameters(df, {"bb_std": 1.6, "mr_atr_stop_mult": 1.5})
    r2 = evaluate_mean_reversion_parameters(df, {"bb_std": 2.5, "mr_atr_stop_mult": 2.5})
    assert (r1.total_trades, round(r1.total_return_pct, 6)) != (r2.total_trades, round(r2.total_return_pct, 6))

    m = evaluate_parameters(df, {"breakout_lookback": 24, "allow_short": False})
    assert all(t.side == "LONG" for t in m.trades)

    pr = evaluate_pairs_parameters(df, _ohlcv(_random_walk(600, seed=4)), {"stat_arb_entry_z": 2.0})
    assert pr.initial_capital == 10000.0


def test_validate_with_evaluator_applies_risk_and_dd_rules():
    class R:
        def __init__(self, ret, sharpe, dd):
            self.total_return_pct, self.sharpe_ratio, self.max_drawdown_pct = ret, sharpe, dd

    def evaluator(params, split):
        good = params.get("tag") == "cand"
        if split == "train":
            return R(5.0, 2.0, 3.0) if good else R(1.0, 0.5, 3.0)
        return R(params.get("oos_ret", 2.0), 1.5, params.get("oos_dd", 4.0)) if good else R(0.5, 0.4, 4.0)

    assert validate_with_evaluator({}, {"tag": "cand"}, evaluator).is_valid is True
    assert validate_with_evaluator({}, {"tag": "cand", "oos_dd": 20.0}, evaluator, max_dd_limit=14.0).is_valid is False
    assert validate_with_evaluator({}, {"tag": "cand", "oos_ret": -5.0}, evaluator).is_valid is False
    bad_risk = validate_with_evaluator({}, {"tag": "cand", "account_risk_pct": 0.08}, evaluator)
    assert bad_risk.is_valid is False and "1%-5%" in bad_risk.reason


def test_rotation_advances_and_persists_without_network(cfg, tmp_path, monkeypatch):
    cfg_path = tmp_path / "active_config.json"
    cfg_path.write_text(json.dumps(cfg))
    state_path = tmp_path / "rotation.json"

    calls = []

    def fake_optimize_task(task, config_path=None, auto_commit=None, data_cache=None, submit_to_governor=True):
        calls.append(mso.task_label(task))
        return {"task": mso.task_label(task), "status": "unchanged"}

    monkeypatch.setattr(mso, "optimize_task", fake_optimize_task)
    n_tasks = len(mso.build_optimization_tasks(cfg))

    r1 = mso.run_multi_strategy_optimization(tasks_per_cycle=2, config_path=cfg_path, state_path=state_path)
    r2 = mso.run_multi_strategy_optimization(tasks_per_cycle=2, config_path=cfg_path, state_path=state_path)

    assert len(r1["results"]) == 2 and len(r2["results"]) == 2
    assert calls[:2] != calls[2:4]  # rotation moved on to different strategies
    state = json.loads(state_path.read_text())
    assert state["next_index"] == 4 % n_tasks
    assert len(state["history"]) == 4

    calls.clear()
    mso.run_multi_strategy_optimization(run_all=True, config_path=cfg_path, state_path=state_path)
    assert len(calls) == n_tasks


def test_one_failing_task_does_not_stop_rotation(cfg, tmp_path, monkeypatch):
    cfg_path = tmp_path / "active_config.json"
    cfg_path.write_text(json.dumps(cfg))

    def flaky(task, **kwargs):
        if task["strategy"] == "momentum":
            raise RuntimeError("boom")
        return {"task": mso.task_label(task), "status": "unchanged"}

    monkeypatch.setattr(mso, "optimize_task", flaky)
    res = mso.run_multi_strategy_optimization(run_all=True, config_path=cfg_path, state_path=tmp_path / "s.json")
    statuses = {r["task"]: r["status"] for r in res["results"]}
    assert statuses["momentum:global"] == "error"
    assert statuses["mean_reversion:global"] == "unchanged"


def test_do_no_harm_gate_blocks_oos_degradation():
    from agent.validator import ValidationReport

    def rep(ret_delta, sharpe, base_sharpe):
        return ValidationReport(
            is_valid=True, reason="Passed", train_return_delta=2.0, test_return_delta=ret_delta,
            train_sharpe=1.0, test_sharpe=sharpe, test_max_dd=2.0,
            base_test_sharpe=base_sharpe, base_test_max_dd=2.0, test_sharpe_delta=sharpe - base_sharpe,
        )

    # Real case observed on macro_hedges: validator passed, but OOS was worse than baseline
    assert mso.oos_degradation_reason(rep(-0.85, -3.15, -1.00)) is not None
    assert mso.oos_degradation_reason(rep(+0.50, -2.00, -1.00)) is not None
    assert mso.oos_degradation_reason(rep(+0.84, -4.22, -6.56)) is None


def test_basis_yield_backtest_and_evaluator():
    from agent.validator import evaluate_basis_yield_parameters
    from core.backtest_engine import run_basis_yield_backtest

    df = _ohlcv(_random_walk(600, seed=7, drift=0.0005))
    r_low = run_basis_yield_backtest(df, hurdle_apr=4.0, max_basis_allocation_pct=0.30)
    r_high = evaluate_basis_yield_parameters(df, {"basis_hurdle_apr": 25.0, "max_basis_allocation_pct": 0.20})
    assert r_low.total_trades >= r_high.total_trades
    assert all(t.strategy_id == "basis_yield" for t in r_low.trades)


def test_trade_analyst_deterministic_proposals_cover_all_strategies():
    from agent.trade_analyst import build_deterministic_analyst_proposals

    stats = {
        "win_rate_pct": 42.0,
        "momentum_pnl": -20.0,
        "momentum_trades": 5,
        "by_strategy": {
            "momentum": {"trades": 5, "pnl": -20.0, "win_rate_pct": 40.0},
            "mean_reversion": {"trades": 4, "pnl": -15.0, "win_rate_pct": 25.0},
            "stat_arb_pairs": {"trades": 3, "pnl": -12.0, "win_rate_pct": 33.0},
            "basis_yield": {"trades": 2, "pnl": -5.0, "win_rate_pct": 0.0},
        },
        "by_symbol": {"TOXIC": {"trades": 3, "wins": 0, "pnl": -75.0, "win_rate": 0.0}},
    }
    props = build_deterministic_analyst_proposals(stats)
    strat_ids = {p["strategy_id"] for p in props}
    assert {"momentum", "mean_reversion", "stat_arb_pairs", "basis_yield", "portfolio_coordinator"} <= strat_ids


def test_governor_merge_assigns_watchlist_symbols_to_profiles():
    from agent.governor import _merge_proposed_changes_into_config

    base = {
        "watchlist": ["SOL/USDT"],
        "asset_profiles": {
            "crypto_micro_swing": {"symbols": ["SOL/USDT"]},
            "crypto_macro": {"symbols": ["BTC/USDT"]},
            "stocks_growth": {"symbols": ["NVDA"]},
            "macro_hedges": {"symbols": ["XLE"]},
        },
    }
    merged = _merge_proposed_changes_into_config(
        base,
        {
            "watchlist": ["SOL/USDT", "BARD/USDT", "BNB/USDT", "SMCI", "KDP"],
            "blacklisted_symbols": ["BTC/USDT"],
        },
    )
    assert "BARD/USDT" in merged["asset_profiles"]["crypto_micro_swing"]["symbols"]
    assert "BNB/USDT" in merged["asset_profiles"]["crypto_macro"]["symbols"]
    assert "BTC/USDT" not in merged["asset_profiles"]["crypto_macro"]["symbols"]
    assert "SMCI" in merged["asset_profiles"]["stocks_growth"]["symbols"]
    assert "KDP" in merged["asset_profiles"]["macro_hedges"]["symbols"]


def test_discover_cointegrated_pairs_adapts_to_changing_watchlist():
    from strategies.stat_arb_pairs import discover_cointegrated_pairs

    base_series = _random_walk(350, seed=101)
    rng = np.random.default_rng(202)
    # Create two stationary cointegrated crypto legs and two stationary stock legs
    c1 = _ohlcv(base_series + rng.normal(0, 0.3, 350))
    c2 = _ohlcv(base_series * 0.5 + rng.normal(0, 0.2, 350))
    s1 = _ohlcv(base_series * 1.2 + rng.normal(0, 0.25, 350))
    s2 = _ohlcv(base_series * 1.2 + rng.normal(0, 0.25, 350))
    divergent = _ohlcv(_random_walk(350, seed=999, drift=-0.002))

    mdata = {
        "AAA/USDT": c1,
        "BBB/USDT": c2,
        "CCC/USDT": divergent,
        "STK1": s1,
        "STK2": s2,
    }
    pairs = discover_cointegrated_pairs(
        ["AAA/USDT", "BBB/USDT", "CCC/USDT", "STK1", "STK2"],
        config={"pairs_lookback": 100, "pairs_min_correlation": 0.50, "pairs_Blacklist": []},
        market_data=mdata,
    )
    assert ["AAA/USDT", "BBB/USDT"] in pairs
    assert ["STK1", "STK2"] in pairs

