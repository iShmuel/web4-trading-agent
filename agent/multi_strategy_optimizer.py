"""Multi-Strategy, Multi-Profile Self-Improvement Optimizer (Agent 11 v2).

The original optimizer only tuned Momentum parameters on a single asset (SOL/USDT).
This module rotates the walk-forward tournament across EVERY live strategy and
EVERY active asset profile:

    momentum        : global defaults  +  each asset profile with active symbols
    mean_reversion  : Bollinger / RSI / ATR-stop parameters
    stat_arb_pairs  : Z-score entry / exit / stop and lookback

Each task:
  1. Loads data for the task's own symbols (or pairs), splits 70% in-sample / 30% out-of-sample.
  2. Searches a bounded parameter grid on IN-SAMPLE data only (risk % is never mutated).
  3. Validates the best candidate on UNSEEN out-of-sample data (agent.validator rules).
  4. Submits a minimal diff to the Constitutional Governor (invariants, pytest gate,
     4-gate auto-approval). Nothing bypasses the Governor.

Safety invariants (AGENTS.md):
  * Paper trading only - this module never touches execution.
  * account_risk_pct is NEVER part of any search grid (1%-5% bounds untouched).
  * All proposed values lie inside agent.governor.PARAMETER_BOUNDS.
"""

from __future__ import annotations

import copy
import itertools
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from core.coordinator import resolve_symbol_config
from core.data_loader import fetch_ohlcv, is_crypto_symbol
from agent.validator import (
    evaluate_basis_yield_parameters,
    evaluate_mean_reversion_parameters,
    evaluate_pairs_parameters,
    evaluate_parameters,
    split_data,
    validate_with_evaluator,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
ROTATION_STATE_PATH = PROJECT_ROOT / "data" / "optimizer_rotation.json"

MAX_SYMBOLS_PER_TASK = 16
MAX_TRAIN_DD_PCT = 12.0
MAX_OOS_DD_PCT = 14.0
MIN_TRAIN_TRADES = 5

# Bounded search grids. account_risk_pct is intentionally absent from every grid.
MOMENTUM_GRID: Dict[str, List[Any]] = {
    "breakout_lookback": [16, 24, 32, 48, 72],
    "ema_trend_filter": [50, 100, 200],
    "atr_multiplier": [1.75, 2.25, 2.75, 3.25],
}
MEAN_REVERSION_GRID: Dict[str, List[Any]] = {
    "bb_std": [1.6, 1.8, 2.0, 2.2, 2.5],
    "bb_period": [20, 30],
    "rsi_oversold": [25, 30, 35],
    "mr_atr_stop_mult": [1.5, 2.0, 2.5],
    "hurst_mr_max": [0.48, 0.52, 0.58, 0.65],
}
PAIRS_GRID: Dict[str, List[Any]] = {
    "stat_arb_entry_z": [2.0, 2.25, 2.5, 2.75, 3.0],
    "stat_arb_exit_z": [0.25, 0.5, 0.75],
    "stat_arb_stop_z": [3.5, 4.0, 4.5],
    "stat_arb_lookback": [48, 72, 96],
}
BASIS_YIELD_GRID: Dict[str, List[Any]] = {
    "basis_hurdle_apr": [6.0, 8.0, 10.0, 12.0, 15.0],
    "max_basis_allocation_pct": [0.20, 0.25, 0.30, 0.35],
}
VOLATILITY_SQUEEZE_GRID: Dict[str, List[Any]] = {
    "squeeze_atr_stop_mult": [1.5, 2.0, 2.5],
    "squeeze_tp_atr_mult": [2.0, 2.5, 3.0],
}
TREND_PULLBACK_GRID: Dict[str, List[Any]] = {
    "pullback_rsi_max": [38.0, 42.0, 44.0, 48.0],
    "pullback_atr_stop_mult": [1.5, 2.0, 2.5],
}
VWAP_REVERSION_GRID: Dict[str, List[Any]] = {
    "vwap_window": [18, 24, 36],
    "vwap_std_mult": [1.6, 1.8, 2.0, 2.2],
    "vwap_rsi_max": [34.0, 38.0, 42.0],
}
NR7_BREAKOUT_GRID: Dict[str, List[Any]] = {
    "nr7_lookback": [5, 7, 9, 12],
    "nr7_atr_stop_mult": [1.5, 2.0, 2.5],
}
RANGE_ENVELOPE_GRID: Dict[str, List[Any]] = {
    "range_window": [24, 36, 48],
    "range_rsi_oversold": [28.0, 30.0, 32.0],
    "range_min_net_profit_usd": [10.0, 15.0],
}

SUPERTREND_CTA_GRID: Dict[str, List[Any]] = {
    "supertrend_atr_period": [8, 10, 14],
    "supertrend_crypto_mult": [2.5, 3.0, 3.5],
    "supertrend_equity_mult": [2.0, 2.5, 3.0],
}

DONCHIAN_RANGE_FADE_GRID: Dict[str, List[Any]] = {
    "donchian_fade_window": [15, 20, 30],
    "donchian_fade_rsi_low": [8.0, 10.0, 15.0],
    "donchian_fade_rsi_high": [85.0, 90.0, 92.0],
}


@dataclass
class AggregateResult:
    """Portfolio-style aggregate across several symbols / pairs."""

    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    total_trades: int


def _aggregate(results: List[Any]) -> AggregateResult:
    if not results:
        return AggregateResult(0.0, 0.0, 0.0, 0)
    n = len(results)
    return AggregateResult(
        total_return_pct=sum(r.total_return_pct for r in results) / n,
        sharpe_ratio=sum(r.sharpe_ratio for r in results) / n,
        max_drawdown_pct=max(r.max_drawdown_pct for r in results),
        total_trades=sum(r.total_trades for r in results),
    )


# ---------------------------------------------------------------------------
# Config / state helpers
# ---------------------------------------------------------------------------
def load_active_config(path: Optional[Path] = None) -> Dict[str, Any]:
    with open(path or CONFIG_PATH, "r") as f:
        return json.load(f)


def _load_rotation_state(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"next_index": 0, "history": []}


def _save_rotation_state(state: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.replace(tmp, path)


def _active_symbols(cfg: Dict[str, Any]) -> List[str]:
    blacklist = set(cfg.get("blacklisted_symbols", []))
    return [s for s in cfg.get("watchlist", []) if s not in blacklist]


def _profile_of(cfg: Dict[str, Any], symbol: str) -> Optional[str]:
    if not cfg.get("use_asset_profiles", True):
        return None
    for name, prof in (cfg.get("asset_profiles") or {}).items():
        if symbol in prof.get("symbols", []):
            return name
    return None


# ---------------------------------------------------------------------------
# Task planning
# ---------------------------------------------------------------------------
def build_optimization_tasks(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the rotation of optimization tasks covering every strategy and active profile."""
    active = _active_symbols(cfg)
    tasks: List[Dict[str, Any]] = []

    crypto_all = [s for s in active if is_crypto_symbol(s)]
    stocks_all = [s for s in active if not is_crypto_symbol(s)]
    mixed_basket = crypto_all[:2] + stocks_all[:2]

    # 1. Momentum on global defaults (unassigned symbols, or representative mixed basket if all are profiled)
    unassigned_syms = [s for s in active if _profile_of(cfg, s) is None][:MAX_SYMBOLS_PER_TASK]
    global_mom_syms = unassigned_syms if unassigned_syms else mixed_basket
    if global_mom_syms:
        tasks.append({"strategy": "momentum", "scope": "global", "symbols": global_mom_syms})

    # 2. Mean Reversion on global defaults (mix of crypto + equities for robustness)
    if mixed_basket:
        tasks.append({"strategy": "mean_reversion", "scope": "global", "symbols": mixed_basket})

    # 3. Stat-Arb Pairs (all cointegrated pairs in pairs_universe)
    pairs = [list(p) for p in cfg.get("pairs_universe", []) if isinstance(p, (list, tuple)) and len(p) == 2]
    if pairs:
        tasks.append({"strategy": "stat_arb_pairs", "scope": "global", "pairs": pairs})

    # 4. Delta-Neutral Basis / Funding Yield (all active crypto perpetuals)
    if crypto_all:
        tasks.append({"strategy": "basis_yield", "scope": "global", "symbols": crypto_all[:MAX_SYMBOLS_PER_TASK]})

    # 4B. New Directional Engines (Volatility Squeeze, Trend Pullback, VWAP Reversion)
    if mixed_basket:
        if cfg.get("enable_volatility_squeeze", True):
            tasks.append({"strategy": "volatility_squeeze", "scope": "global", "symbols": mixed_basket})
        if cfg.get("enable_trend_pullback", True):
            tasks.append({"strategy": "trend_pullback", "scope": "global", "symbols": mixed_basket})
        if cfg.get("enable_vwap_reversion", True):
            tasks.append({"strategy": "vwap_reversion", "scope": "global", "symbols": mixed_basket})
        if cfg.get("enable_nr7_breakout", True):
            tasks.append({"strategy": "nr7_breakout", "scope": "global", "symbols": mixed_basket})
        if cfg.get("enable_range_envelope", True):
            tasks.append({"strategy": "range_envelope", "scope": "global", "symbols": mixed_basket})
        if cfg.get("enable_supertrend_cta", True):
            tasks.append({"strategy": "supertrend_cta", "scope": "global", "symbols": mixed_basket})
        if cfg.get("enable_donchian_range_fade", True):
            tasks.append({"strategy": "donchian_range_fade", "scope": "global", "symbols": mixed_basket})

    # 5. Per-Asset-Profile Momentum AND Mean Reversion (all active symbols in each profile)
    for name, prof in (cfg.get("asset_profiles") or {}).items():
        syms = [s for s in prof.get("symbols", []) if s in active][:MAX_SYMBOLS_PER_TASK]
        if syms and cfg.get("use_asset_profiles", True):
            tasks.append({"strategy": "momentum", "scope": name, "symbols": syms})
            tasks.append({"strategy": "mean_reversion", "scope": name, "symbols": syms})

    return tasks


def task_label(task: Dict[str, Any]) -> str:
    return f"{task['strategy']}:{task['scope']}"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def _load_split(symbol: str, timeframe: str, cache: Dict[str, Any]) -> Optional[Tuple[pd.DataFrame, pd.DataFrame]]:
    if symbol in cache:
        return cache[symbol]
    days = 90 if is_crypto_symbol(symbol) else 180
    try:
        df = fetch_ohlcv(symbol=symbol, timeframe=timeframe, days=days)
    except Exception as e:  # network / provider failure: skip symbol, never crash the live loop
        print(f"[MultiOptimizer] Data fetch failed for {symbol}: {e}")
        cache[symbol] = None
        return None
    if df is None or len(df) < 300:
        cache[symbol] = None
        return None
    cache[symbol] = split_data(df, train_ratio=0.7)
    return cache[symbol]


# ---------------------------------------------------------------------------
# Candidate generation (always full-config deep copies; only grid keys mutate)
# ---------------------------------------------------------------------------
def _grid_combos(grid: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    keys = list(grid.keys())
    return [dict(zip(keys, vals)) for vals in itertools.product(*(grid[k] for k in keys))]


def _apply(cfg: Dict[str, Any], scope: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    cand = copy.deepcopy(cfg)
    if scope == "global":
        cand.update(changes)
    else:
        cand.setdefault("asset_profiles", {}).setdefault(scope, {}).update(changes)
    return cand


def _valid_combo(strategy: str, combo: Dict[str, Any]) -> bool:
    if strategy == "stat_arb_pairs":
        return combo["stat_arb_stop_z"] >= combo["stat_arb_entry_z"] + 0.75
    return True


def oos_degradation_reason(report: Any) -> Optional[str]:
    """Do-No-Harm gate: a candidate must not be worse than the CURRENT config on unseen data.

    The shared validator only rejects absolute OOS losses below -2%; this gate additionally
    requires OOS return delta >= 0 and OOS Sharpe delta >= 0 versus the live baseline, so
    proposals that would fail Governor Gate 4 never reach the human approval queue.
    """
    if report.test_return_delta < 0:
        return f"Failed: Out-of-sample return worse than current baseline ({report.test_return_delta:+.2f}%)."
    if report.test_sharpe_delta < 0:
        return (
            f"Failed: Out-of-sample Sharpe worse than current baseline "
            f"({report.test_sharpe:.2f} < {report.base_test_sharpe:.2f})."
        )
    return None


# ---------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------
def _make_evaluator(task: Dict[str, Any], cfg: Dict[str, Any], cache: Dict[str, Any]) -> Optional[Callable]:
    timeframe = cfg.get("timeframe", "1h")
    strategy = task["strategy"]

    if strategy in ("momentum", "mean_reversion"):
        data = {s: _load_split(s, timeframe, cache) for s in task["symbols"]}
        data = {s: d for s, d in data.items() if d is not None}
        if not data:
            return None
        task["symbols_used"] = list(data.keys())
        fn = evaluate_parameters if strategy == "momentum" else evaluate_mean_reversion_parameters
        scope = task.get("scope", "global")

        def evaluator(full_cfg: Dict[str, Any], split: str) -> AggregateResult:
            idx = 0 if split == "train" else 1
            eff_cfg = {**full_cfg, "use_asset_profiles": False} if scope == "global" else full_cfg
            return _aggregate([fn(d[idx], resolve_symbol_config(eff_cfg, s)) for s, d in data.items()])

        return evaluator

    if strategy == "basis_yield":
        data = {s: _load_split(s, timeframe, cache) for s in task["symbols"]}
        data = {s: d for s, d in data.items() if d is not None}
        if not data:
            return None
        task["symbols_used"] = list(data.keys())

        def evaluator(full_cfg: Dict[str, Any], split: str) -> AggregateResult:
            idx = 0 if split == "train" else 1
            return _aggregate([evaluate_basis_yield_parameters(d[idx], full_cfg) for d in data.values()])

        return evaluator

    if strategy == "stat_arb_pairs":
        pair_data = []
        for a, b in task["pairs"]:
            da, db = _load_split(a, timeframe, cache), _load_split(b, timeframe, cache)
            if da is not None and db is not None:
                pair_data.append(((a, b), da, db))
        if not pair_data:
            return None
        task["pairs_used"] = [f"{a}:{b}" for (a, b), _, _ in pair_data]

        def evaluator(full_cfg: Dict[str, Any], split: str) -> AggregateResult:
            idx = 0 if split == "train" else 1
            return _aggregate([evaluate_pairs_parameters(da[idx], db[idx], full_cfg) for _, da, db in pair_data])

        return evaluator

    if strategy in ("volatility_squeeze", "trend_pullback", "vwap_reversion", "nr7_breakout", "range_envelope", "supertrend_cta", "donchian_range_fade"):
        from core.backtest_engine import (
            run_volatility_squeeze_backtest,
            run_trend_pullback_backtest,
            run_vwap_reversion_backtest,
            run_nr7_breakout_backtest,
            run_range_envelope_backtest,
            run_supertrend_cta_backtest,
            run_donchian_range_fade_backtest,
        )
        from core.data_loader import is_crypto_symbol as _is_crypto
        data = {s: _load_split(s, timeframe, cache) for s in task["symbols"]}
        data = {s: d for s, d in data.items() if d is not None}
        if not data:
            return None
        task["symbols_used"] = list(data.keys())

        def evaluator(full_cfg: Dict[str, Any], split: str) -> AggregateResult:
            idx = 0 if split == "train" else 1
            results = []
            for sym_k, d in data.items():
                if strategy == "volatility_squeeze":
                    results.append(run_volatility_squeeze_backtest(
                        d[idx],
                        stop_atr_mult=float(full_cfg.get("squeeze_atr_stop_mult", 2.0)),
                        tp_atr_mult=float(full_cfg.get("squeeze_tp_atr_mult", 2.5)),
                    ))
                elif strategy == "trend_pullback":
                    results.append(run_trend_pullback_backtest(
                        d[idx],
                        rsi_pullback_max=float(full_cfg.get("pullback_rsi_max", 44.0)),
                        stop_atr_mult=float(full_cfg.get("pullback_atr_stop_mult", 2.0)),
                    ))
                elif strategy == "nr7_breakout":
                    results.append(run_nr7_breakout_backtest(
                        d[idx],
                        nr7_lookback=int(full_cfg.get("nr7_lookback", 7)),
                        stop_atr_mult=float(full_cfg.get("nr7_atr_stop_mult", 2.0)),
                    ))
                elif strategy == "range_envelope":
                    results.append(run_range_envelope_backtest(
                        d[idx],
                        range_window=int(full_cfg.get("range_window", 36)),
                        rsi_oversold=float(full_cfg.get("range_rsi_oversold", 30.0)),
                        min_net_profit_usd=float(full_cfg.get("range_min_net_profit_usd", 10.0)),
                    ))
                elif strategy == "supertrend_cta":
                    st_mult = (
                        float(full_cfg.get("supertrend_crypto_mult", 3.0))
                        if _is_crypto(sym_k)
                        else float(full_cfg.get("supertrend_equity_mult", 2.5))
                    )
                    results.append(run_supertrend_cta_backtest(
                        d[idx],
                        atr_period=int(full_cfg.get("supertrend_atr_period", 10)),
                        mult=st_mult,
                        stop_atr_mult=float(full_cfg.get("supertrend_stop_atr_mult", 3.0)),
                    ))
                elif strategy == "donchian_range_fade":
                    results.append(run_donchian_range_fade_backtest(
                        d[idx],
                        window=int(full_cfg.get("donchian_fade_window", 20)),
                        rsi_low=float(full_cfg.get("donchian_fade_rsi_low", 10.0)),
                        rsi_high=float(full_cfg.get("donchian_fade_rsi_high", 90.0)),
                    ))
                else:
                    results.append(run_vwap_reversion_backtest(
                        d[idx],
                        vwap_window=int(full_cfg.get("vwap_window", 24)),
                        std_mult=float(full_cfg.get("vwap_std_mult", 1.8)),
                        rsi_max=float(full_cfg.get("vwap_rsi_max", 38.0)),
                    ))
            return _aggregate(results)

        return evaluator

    return None


# ---------------------------------------------------------------------------
# One task = one walk-forward tournament
# ---------------------------------------------------------------------------
def optimize_task(
    task: Dict[str, Any],
    config_path: Optional[Path] = None,
    auto_commit: Optional[bool] = True,
    data_cache: Optional[Dict[str, Any]] = None,
    submit_to_governor: bool = True,
) -> Dict[str, Any]:
    cfg = load_active_config(config_path)
    label = task_label(task)
    cache = data_cache if data_cache is not None else {}
    strategy, scope = task["strategy"], task["scope"]
    grid = {
        "momentum": MOMENTUM_GRID,
        "mean_reversion": MEAN_REVERSION_GRID,
        "stat_arb_pairs": PAIRS_GRID,
        "basis_yield": BASIS_YIELD_GRID,
        "volatility_squeeze": VOLATILITY_SQUEEZE_GRID,
        "trend_pullback": TREND_PULLBACK_GRID,
        "vwap_reversion": VWAP_REVERSION_GRID,
        "nr7_breakout": NR7_BREAKOUT_GRID,
        "range_envelope": RANGE_ENVELOPE_GRID,
        "supertrend_cta": SUPERTREND_CTA_GRID,
        "donchian_range_fade": DONCHIAN_RANGE_FADE_GRID,
    }[strategy]

    print("=" * 65)
    print(f"   MULTI-STRATEGY SELF-IMPROVEMENT: {label}")
    print("=" * 65)

    evaluator = _make_evaluator(task, cfg, cache)
    if evaluator is None:
        return {"task": label, "status": "skipped", "reason": "No usable market data"}
    universe = task.get("symbols_used") or task.get("pairs_used") or []
    print(f"[Universe] {', '.join(universe)}")

    base_train = evaluator(cfg, "train")
    base_test = evaluator(cfg, "test")
    print(f"[Baseline IS]  Ret {base_train.total_return_pct:+.2f}% | Sharpe {base_train.sharpe_ratio:.2f} | "
          f"MaxDD {base_train.max_drawdown_pct:.1f}% | Trades {base_train.total_trades}")
    print(f"[Baseline OOS] Ret {base_test.total_return_pct:+.2f}% | Sharpe {base_test.sharpe_ratio:.2f} | "
          f"MaxDD {base_test.max_drawdown_pct:.1f}% | Trades {base_test.total_trades}")

    best_changes: Optional[Dict[str, Any]] = None
    best_sharpe = base_train.sharpe_ratio
    tested = 0
    for combo in _grid_combos(grid):
        if not _valid_combo(strategy, combo):
            continue
        tested += 1
        res = evaluator(_apply(cfg, scope, combo), "train")
        if (
            res.sharpe_ratio > best_sharpe
            and res.max_drawdown_pct < MAX_TRAIN_DD_PCT
            and res.total_trades >= MIN_TRAIN_TRADES
        ):
            best_sharpe, best_changes = res.sharpe_ratio, combo
    print(f"[Search] Tested {tested} in-sample permutations.")

    if best_changes is None:
        print("[Result] Current parameters already optimal in-sample. No mutation proposed.")
        return {"task": label, "status": "unchanged", "reason": "No candidate beat baseline in-sample"}

    # Only propose keys that actually change
    current = cfg if scope == "global" else resolve_symbol_config(cfg, universe[0].split(":")[0])
    diff = {k: v for k, v in best_changes.items() if current.get(k) != v}
    if not diff:
        return {"task": label, "status": "unchanged", "reason": "Best candidate equals current parameters"}

    candidate_cfg = _apply(cfg, scope, diff)
    report = validate_with_evaluator(cfg, candidate_cfg, evaluator, max_dd_limit=MAX_OOS_DD_PCT)
    print(f"[Candidate] {diff}")
    print(f"[Validation Report] {report.reason}")
    print(f"  Train Return Delta: {report.train_return_delta:+.2f}% | Test Return Delta: {report.test_return_delta:+.2f}% "
          f"| OOS MaxDD: {report.test_max_dd:.1f}%")

    summary = {
        "task": label,
        "candidate": diff,
        "train_return_delta": round(report.train_return_delta, 2),
        "test_return_delta": round(report.test_return_delta, 2),
        "test_sharpe": round(report.test_sharpe, 2),
        "base_test_sharpe": round(report.base_test_sharpe, 2),
        "test_max_dd": round(report.test_max_dd, 2),
    }
    if not report.is_valid:
        print("[Safety Gate] Candidate rejected by walk-forward validator. Retaining current config.")
        return {**summary, "status": "rejected", "reason": report.reason}

    degradation = oos_degradation_reason(report)
    if degradation:
        print(f"[Do-No-Harm Gate] {degradation} Retaining current config.")
        return {**summary, "status": "rejected", "reason": degradation}

    proposed_changes = diff if scope == "global" else {"asset_profiles": {scope: diff}}
    if not submit_to_governor:
        return {**summary, "status": "validated", "proposed_changes": proposed_changes}

    from agent.governor import review_and_deploy_proposal

    gov_res = review_and_deploy_proposal(
        proposed_changes=proposed_changes,
        rationale=(
            f"Multi-strategy walk-forward winner for {label} on {', '.join(universe)}: "
            f"IS Sharpe {report.train_sharpe:.2f} ({report.train_return_delta:+.2f}%), "
            f"OOS Sharpe {report.test_sharpe:.2f} vs base {report.base_test_sharpe:.2f} ({report.test_return_delta:+.2f}%)"
        ),
        empirical_proof={
            "target": label,
            "universe": universe,
            "train_sharpe": report.train_sharpe,
            "test_sharpe": report.test_sharpe,
            "train_return_delta": report.train_return_delta,
            "test_return_delta": report.test_return_delta,
            "test_max_dd": report.test_max_dd,
            "base_test_sharpe": report.base_test_sharpe,
            "base_test_max_dd": report.base_test_max_dd,
            "test_sharpe_delta": report.test_sharpe_delta,
        },
        config_path=config_path,
        verify_tests=True,
        use_llm=False,
        auto_commit=auto_commit,
    )
    status_map = {"DEPLOYED": "deployed", "PENDING_HUMAN_APPROVAL": "pending_human_approval"}
    status = status_map.get(gov_res.status, "deployed" if gov_res.approved else "rolled_back")
    print(f"[Governor] {gov_res.status} ({gov_res.proposal_id})")
    return {**summary, "status": status, "proposal_id": gov_res.proposal_id, "governor_status": gov_res.status}


# ---------------------------------------------------------------------------
# Rotation driver (called by the live loop every N hours)
# ---------------------------------------------------------------------------
def run_multi_strategy_optimization(
    auto_commit: Optional[bool] = True,
    tasks_per_cycle: Optional[int] = None,
    run_all: bool = False,
    config_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run the next ``tasks_per_cycle`` tasks of the rotation (or all of them)."""
    cfg = load_active_config(config_path)
    tasks = build_optimization_tasks(cfg)
    if not tasks:
        return {"status": "skipped", "results": [], "reason": "No optimization tasks available"}

    s_path = state_path or ROTATION_STATE_PATH
    state = _load_rotation_state(s_path)
    n = len(tasks) if run_all else max(1, int(tasks_per_cycle or cfg.get("optimizer_tasks_per_cycle", 4)))
    n = min(n, len(tasks))
    start = int(state.get("next_index", 0)) % len(tasks)
    selected = [tasks[(start + i) % len(tasks)] for i in range(n)]

    cache: Dict[str, Any] = {}
    results = []
    for task in selected:
        try:
            res = optimize_task(task, config_path=config_path, auto_commit=auto_commit, data_cache=cache)
        except Exception as e:  # one failing strategy must never stop the others or the live loop
            res = {"task": task_label(task), "status": "error", "reason": str(e)}
        res["timestamp"] = datetime.now(timezone.utc).isoformat()
        results.append(res)

    state["next_index"] = (start + n) % len(tasks)
    state["rotation"] = [task_label(t) for t in tasks]
    state["history"] = (state.get("history", []) + results)[-100:]
    _save_rotation_state(state, s_path)

    statuses = {r["status"] for r in results}
    overall = "deployed" if "deployed" in statuses else (
        "pending_human_approval" if "pending_human_approval" in statuses else "completed"
    )
    return {"status": overall, "results": results}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Multi-strategy walk-forward optimizer")
    parser.add_argument("--all", action="store_true", help="Run every task in the rotation now")
    parser.add_argument("--dry-run", action="store_true", help="Validate only; never submit to the Governor")
    args = parser.parse_args()

    if args.dry_run:
        _cfg = load_active_config()
        _cache: Dict[str, Any] = {}
        for _t in build_optimization_tasks(_cfg):
            _r = optimize_task(_t, auto_commit=False, data_cache=_cache, submit_to_governor=False)
            print(json.dumps(_r, default=str))
    else:
        print(json.dumps(run_multi_strategy_optimization(auto_commit=True, run_all=args.all), indent=2, default=str))
