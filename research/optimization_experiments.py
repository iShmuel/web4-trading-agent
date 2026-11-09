"""Controlled optimization experiments on the existing coordinator replay engine.

Re-uses research/run_shifted_oos_validation.py's bar-by-bar PortfolioCoordinator
replay (taker 0.06% + 0.04% slippage per side, closed-bar causal indicators) and
runs every scenario on BOTH 90-day windows of the 180-day history:

    W1 = days -180..-90   (in-sample for any parameter *selection*)
    W2 = days  -90..0     (held-out; never used to pick a candidate)

Universes
    live14 : the deployed watchlist in config/active_config.json (what the bot trades)
    oos12  : the W1-frozen universe from reports/oos_validation_report.json

Decision rule (fixed BEFORE running, to limit data-mining): a candidate change is
only "supported" if it improves net return AND does not worsen max drawdown on
BOTH windows of BOTH universes versus the control run in the same grid. All
trial counts are recorded so the multiple-testing burden is explicit.

Caveat that applies to every number here: radars are stubbed offline (VIX 16,
funding constant 0.012%/8h) exactly as in the original OOS script.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "research"))

import run_shifted_oos_validation as oos  # noqa: E402

CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
OOS_REPORT = PROJECT_ROOT / "reports" / "oos_validation_report.json"
OUT = PROJECT_ROOT / "reports" / "optimization_experiments.json"

ALL4 = ["momentum", "mean_reversion", "stat_arb_pairs", "basis_yield"]
_FRAMES: Optional[Dict[str, Any]] = None


def apply_overrides(cfg: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Apply overrides at top level AND inside every asset profile that defines the key
    (profile keys shadow top-level keys, so a top-level-only override would silently no-op)."""
    out = json.loads(json.dumps(cfg))
    for k, v in overrides.items():
        out[k] = v
        for prof in (out.get("asset_profiles") or {}).values():
            if k in prof:
                prof[k] = v
    return out


def _frames(base_cfg: Dict[str, Any]) -> Dict[str, Any]:
    global _FRAMES
    if _FRAMES is None:
        _FRAMES = oos.load_180d_candidate_frames(base_cfg)
    return _FRAMES


def run_scenario(spec: Dict[str, Any]) -> Dict[str, Any]:
    base_cfg = json.loads(CONFIG_PATH.read_text())
    cfg = apply_overrides(base_cfg, spec.get("overrides", {}))
    frames = _frames(base_cfg)
    win1, win2, _, _, _ = oos.split_selection_and_oos_windows(frames, cfg)
    if spec["universe"] == "live14":
        universe = [s for s in base_cfg["watchlist"] if s in win2]
    else:
        universe = json.loads(OOS_REPORT.read_text())["methodology"]["frozen_selected_universe"]
    res: Dict[str, Any] = {"name": spec["name"], "group": spec["group"], "universe": spec["universe"],
                           "strategies": spec["strategies"], "overrides": spec.get("overrides", {})}
    t0 = time.time()
    for wname, wf in (("W1", win1), ("W2", win2)):
        m = oos.run_coordinator_window_replay(wf, universe, cfg, spec["strategies"])
        res[wname] = {k: m.get(k) for k in (
            "net_return_pct", "directional_only_return_pct", "sharpe_ratio", "sortino_ratio",
            "max_drawdown_pct", "total_trades", "directional_trades", "profit_factor",
            "directional_profit_factor", "directional_win_rate_pct",
            "directional_expectancy_per_trade", "total_fees_paid", "net_pnl")}
        res[wname]["by_strategy"] = m.get("by_strategy")
    res["seconds"] = round(time.time() - t0, 1)
    return res


def build_specs() -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []

    def add(name: str, group: str, strategies: List[str], overrides: Dict[str, Any], universes=("live14", "oos12")):
        for u in universes:
            specs.append({"name": name, "group": group, "universe": u, "strategies": strategies, "overrides": overrides})

    # --- control / strategy attribution (same code, same costs) ---
    add("ctrl_all4", "control", ALL4, {})
    add("ctrl_momentum", "control", ["momentum"], {})
    add("ctrl_mean_reversion", "control", ["mean_reversion"], {})
    add("ctrl_pairs", "control", ["stat_arb_pairs"], {}, universes=("oos12",))
    add("ctrl_basis", "control", ["basis_yield"], {})
    add("combo_mom_mr", "combination", ["momentum", "mean_reversion"], {})
    add("combo_mom_only_plus_basis", "combination", ["momentum", "basis_yield"], {})
    add("combo_no_pairs", "combination", ["momentum", "mean_reversion", "basis_yield"], {})
    add("combo_no_mr", "combination", ["momentum", "stat_arb_pairs", "basis_yield"], {}, universes=("oos12",))

    # --- stat-arb economics ---
    for r in (1.0, 2.0, 3.0, 5.0):
        add(f"pairs_costgate_{r}", "pairs", ["stat_arb_pairs"], {"stat_arb_min_edge_cost_ratio": r}, universes=("oos12",))
    for z in (2.5, 3.0):
        add(f"pairs_entryz_{z}", "pairs", ["stat_arb_pairs"], {"stat_arb_entry_z": z}, universes=("oos12",))

    # --- momentum one-at-a-time sensitivity (momentum-only so the effect is not diluted) ---
    mom = [
        ("lookback_24", {"breakout_lookback": 24}), ("lookback_36", {"breakout_lookback": 36}),
        ("lookback_72", {"breakout_lookback": 72}), ("atrmult_1.75", {"atr_multiplier": 1.75}),
        ("atrmult_3.0", {"atr_multiplier": 3.0}), ("adxchop_18", {"adx_chop_threshold": 18.0}),
        ("adxchop_26", {"adx_chop_threshold": 26.0}), ("no_breakeven", {"enable_breakeven_lock": False}),
        ("breakeven_2.0", {"breakeven_atr_mult": 2.0}), ("no_partial_tp", {"take_profit_ratio": 0.0, "take_profit_atr_mult": 0.0}),
        ("tp_atr_4.0", {"take_profit_atr_mult": 4.0}), ("no_regime_filter", {"enable_regime_filter": False}),
        ("ema_100", {"ema_trend_filter": 100}),
    ]
    for n, o in mom:
        add(f"mom_{n}", "momentum_sens", ["momentum"], o)

    # --- mean-reversion one-at-a-time sensitivity ---
    mr = [
        ("rsi_25", {"rsi_oversold": 25}), ("rsi_35", {"rsi_oversold": 35}),
        ("bbstd_1.8", {"bb_std": 1.8}), ("bbstd_2.5", {"bb_std": 2.5}),
        ("stop_1.5", {"mr_atr_stop_mult": 1.5}), ("stop_3.0", {"mr_atr_stop_mult": 3.0}),
        ("hold_24", {"mr_max_hold_bars": 24}), ("hold_96", {"mr_max_hold_bars": 96}),
        ("adx_thr_20", {"adx_threshold": 20}), ("adx_thr_30", {"adx_threshold": 30}),
        ("no_sweep", {"enable_liquidity_sweep": False}), ("no_short", {"mr_allow_short": False}),
    ]
    for n, o in mr:
        add(f"mr_{n}", "mr_sens", ["mean_reversion"], o)
    return specs


def main(workers: int = 8) -> Dict[str, Any]:
    specs = build_specs()
    print(f"[experiments] {len(specs)} scenarios x 2 windows = {2 * len(specs)} replays, {workers} workers", flush=True)
    t0 = time.time()
    results: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(run_scenario, specs), 1):
            results.append(r)
            print(f"  [{i:>3}/{len(specs)}] {r['universe']:<6} {r['name']:<28} "
                  f"W1 {r['W1']['net_return_pct']:+6.2f}% dd{r['W1']['max_drawdown_pct']:5.2f} | "
                  f"W2 {r['W2']['net_return_pct']:+6.2f}% dd{r['W2']['max_drawdown_pct']:5.2f} "
                  f"tr{r['W2']['total_trades']:>4} ({r['seconds']}s)", flush=True)
    out = {
        "decision_rule": "Adopt only if net return improves and max drawdown does not worsen on BOTH windows of BOTH universes vs control.",
        "trial_count": len(specs),
        "stub_caveat": "VIX/funding/news/SEC radars stubbed offline; funding constant 0.012%/8h",
        "elapsed_seconds": round(time.time() - t0, 1),
        "results": results,
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(f"saved {OUT}  ({out['elapsed_seconds']}s)")
    return out


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8)
