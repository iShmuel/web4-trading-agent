"""Reproducible Git Baseline (861f98e) vs Current Working Tree Performance Comparison Suite.

Executes a fair, controlled, side-by-side backtest comparison between:
  VERSION A: Current Working Version (<repo_root>)
  VERSION B: Git Baseline commit 861f98e (/tmp/web4_baseline_861f98e)

Outputs:
  reports/git_baseline_comparison/comparison_results.json
  reports/git_baseline_comparison/strategy_comparison.json
"""

import copy
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = Path("<repo_root>").resolve()
BASELINE_ROOT = Path("/tmp/web4_baseline_861f98e").resolve()
OUT_DIR = PROJECT_ROOT / "reports" / "git_baseline_comparison"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PYTHON_EXE = sys.executable

WORKER_SCRIPT = OUT_DIR / "comparison_worker.py"


def generate_worker_script() -> None:
    """Generate worker script writing cleanly to JSON output file."""
    content = '''"""Worker script for single-version execution."""
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

target_root = Path(sys.argv[1]).resolve()
out_json_path = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(target_root))

import core.coordinator as coord_mod
from core.coordinator import PortfolioCoordinator, resolve_symbol_config
from core.data_loader import fetch_ohlcv, is_crypto_symbol
from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.backtest_engine import run_backtest, run_mean_reversion_backtest
from core.institutional_stress_tester import calculate_psr_and_dsr
import research.run_shifted_oos_validation as oos

# Load configuration and payload from stdin
payload = json.loads(sys.stdin.read())
cost_scenario = payload.get("cost_scenario", {"fee_pct": 0.0006, "slippage_pct": 0.0004})
overrides = payload.get("overrides", {})
strategy_filter = payload.get("strategy_filter", None)

fee_val = float(cost_scenario.get("fee_pct", 0.0006))
slip_val = float(cost_scenario.get("slippage_pct", 0.0004))

# Ensure module-level fee constants match scenario across all versions
oos.TAKER_FEE = fee_val
oos.TAKER_SLIP = slip_val

base_cfg_path = target_root / "config" / "active_config.json"
with open(base_cfg_path, "r") as f:
    cfg = json.load(f)

for k, v in overrides.items():
    cfg[k] = v
    for s_cfg in cfg.get("symbol_configs", {}).values():
        if isinstance(s_cfg, dict):
            s_cfg[k] = v

cfg["fee_pct"] = fee_val
cfg["slippage_pct"] = slip_val

frames = oos.load_180d_candidate_frames(cfg)
win1, win2, min_ts, split_ts, max_ts = oos.split_selection_and_oos_windows(frames, cfg)
universe, scores = oos.select_universe_from_window1(win1, cfg)

enabled_strategies = [strategy_filter] if strategy_filter else ["momentum", "mean_reversion", "stat_arb_pairs", "basis_yield"]

t0 = time.time()
replay = oos.run_coordinator_window_replay(
    window_frames=win2,
    universe=universe,
    base_cfg=cfg,
    enabled_strategies=enabled_strategies,
    initial_capital=10000.0,
)
elapsed = round(time.time() - t0, 2)

net_ret = float(replay.get("net_return_pct", 0.0))
max_dd = float(replay.get("max_drawdown_pct", 0.0))
# Annualized Calmar ratio
ann_ret = net_ret * (365.0 / 90.0)
calmar = round(ann_ret / max_dd, 2) if max_dd > 0 else (99.0 if ann_ret > 0 else 0.0)

# Statistical validation on equity curve
eq_curve = replay.get("equity_curve", [])
psr_stat = 0.0
dsr_stat = 0.0
skew_stat = 0.0
kurt_stat = 0.0
if eq_curve:
    eq_vals = [e[1] if isinstance(e, (list, tuple)) else e.get("equity", 10000.0) for e in eq_curve]
    eq_series = pd.Series(eq_vals)
    hourly_returns = eq_series.pct_change().dropna().values
    if len(hourly_returns) >= 10:
        psr, dsr, exp_max_sr, stats = calculate_psr_and_dsr(
            returns=hourly_returns,
            annualization_factor=252.0 * 6.5,
            num_trials=72,
            benchmark_sr=0.0,
        )
        psr_stat = float(psr)
        dsr_stat = float(dsr)
        skew_stat = float(stats.get("skewness", 0.0))
        kurt_stat = float(stats.get("kurtosis", 0.0))

out = {
    "net_return_pct": net_ret,
    "net_pnl": float(replay.get("net_pnl", 0.0)),
    "ending_equity": float(replay.get("final_equity", 10000.0)),
    "sharpe_ratio": float(replay.get("sharpe_ratio", 0.0)),
    "sortino_ratio": float(replay.get("sortino_ratio", 0.0)),
    "calmar_ratio": calmar,
    "max_drawdown_pct": max_dd,
    "total_trades": int(replay.get("total_trades", 0)),
    "directional_trades": int(replay.get("directional_trades", 0)),
    "basis_accruals": int(replay.get("basis_accruals", 0)),
    "win_rate_pct": float(replay.get("win_rate_pct", 0.0)),
    "directional_win_rate_pct": float(replay.get("directional_win_rate_pct", 0.0)),
    "profit_factor": float(replay.get("profit_factor", 0.0)),
    "directional_profit_factor": float(replay.get("directional_profit_factor", 0.0)),
    "expectancy_per_trade": float(replay.get("expectancy_per_trade", 0.0)),
    "directional_expectancy_per_trade": float(replay.get("directional_expectancy_per_trade", 0.0)),
    "total_fees_paid": float(replay.get("total_fees_paid", 0.0)),
    "elapsed_seconds": elapsed,
    "by_strategy": replay.get("by_strategy", {}),
    "signals_by_strategy": replay.get("signals_by_strategy", {}),
    "risk_rejections": int(replay.get("risk_rejections", 0)),
    "psr": round(psr_stat, 4),
    "dsr": round(dsr_stat, 4),
    "skewness": round(skew_stat, 3),
    "kurtosis": round(kurt_stat, 3),
}

with open(out_json_path, "w") as f:
    json.dump(out, f, indent=2)
'''
    with open(WORKER_SCRIPT, "w") as f:
        f.write(content)


def run_worker(target_dir: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run worker writing output to a temporary JSON file."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        cmd = [PYTHON_EXE, str(WORKER_SCRIPT), str(target_dir), str(tmp_path)]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate(input=json.dumps(payload))
        if proc.returncode != 0:
            raise RuntimeError(f"Worker failed for {target_dir}:\nStderr: {stderr}\nStdout: {stdout}")

        if not tmp_path.exists() or tmp_path.stat().st_size == 0:
            raise RuntimeError(f"Worker produced empty output file: {tmp_path}\nStderr: {stderr}\nStdout: {stdout}")

        with open(tmp_path, "r") as f:
            return json.load(f)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


def benchmark_engineering_components() -> Dict[str, Any]:
    """Benchmark data loading, journal rotation, SEC cache, and memory efficiency."""
    benchmarks = {}

    # 1. Concurrent OHLCV Fetching vs Serial
    sys.path.insert(0, str(PROJECT_ROOT))
    import core.data_loader as dl_mod
    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "NVDA", "SPY", "QQQ"]

    t0 = time.time()
    batch_res = dl_mod.fetch_ohlcv_batch(symbols, timeframe="1h", days=30)
    batch_time = round(time.time() - t0, 3)

    t0 = time.time()
    serial_res = {}
    for s in symbols:
        serial_res[s] = dl_mod.fetch_ohlcv(s, timeframe="1h", days=30)
    serial_time = round(time.time() - t0, 3)

    benchmarks["data_loader"] = {
        "symbols_count": len(symbols),
        "batch_fetch_seconds": batch_time,
        "serial_fetch_seconds": serial_time,
        "speedup_factor": round(serial_time / batch_time, 2) if batch_time > 0 else 1.0,
        "batch_keys_loaded": len(batch_res),
        "serial_keys_loaded": len(serial_res),
        "consistency_match": bool(len(batch_res) == len(serial_res)),
    }

    # 2. Journal Tail Reading Benchmark
    import core.journal as jnl_mod
    jpath = PROJECT_ROOT / "data" / "trade_journal.jsonl"
    if jpath.exists():
        t0 = time.time()
        tail_entries = jnl_mod.load_recent_settled_entries(limit=50)
        tail_time = round((time.time() - t0) * 1000.0, 3)

        t0 = time.time()
        with open(jpath, "r", encoding="utf-8") as f:
            all_lines = [json.loads(line) for line in f if line.strip()]
        full_time = round((time.time() - t0) * 1000.0, 3)

        benchmarks["journal"] = {
            "tail_block_read_ms": tail_time,
            "full_scan_read_ms": full_time,
            "speedup_factor": round(full_time / tail_time, 2) if tail_time > 0 else 1.0,
            "entries_retrieved": len(tail_entries),
            "total_journal_lines": len(all_lines),
        }
    else:
        benchmarks["journal"] = {"status": "journal file not found"}

    # 3. SEC Disk Caching Benchmark
    import core.skill_intelligence_gate as sec_mod
    gate = sec_mod.SecFundamentalGate()
    t0 = time.time()
    v1 = gate.evaluate_equity("NVDA")
    first_call_ms = round((time.time() - t0) * 1000.0, 2)

    t0 = time.time()
    v2 = gate.evaluate_equity("NVDA")
    cached_call_ms = round((time.time() - t0) * 1000.0, 2)

    benchmarks["sec_cache"] = {
        "uncached_or_first_call_ms": first_call_ms,
        "cached_disk_call_ms": cached_call_ms,
        "cache_speedup": round(first_call_ms / cached_call_ms, 2) if cached_call_ms > 0 else 1.0,
        "verdict_consistent": bool(v1.passed == v2.passed and v1.quality_score == v2.quality_score),
    }

    return benchmarks


def main() -> None:
    print("=" * 80)
    print("WEB4 INDEPENDENT PERFORMANCE COMPARISON: VERSION A vs VERSION B")
    print("Version A: Current Working Tree (HEAD 9d9d7b1)")
    print("Version B: Git Baseline (861f98e)")
    print("=" * 80)

    generate_worker_script()

    # Cost scenarios
    scenarios = [
        ("standard_1.0x", {"fee_pct": 0.0006, "slippage_pct": 0.0004}),
        ("friction_1.5x", {"fee_pct": 0.0009, "slippage_pct": 0.0006}),
        ("friction_2.0x", {"fee_pct": 0.0012, "slippage_pct": 0.0008}),
        ("slippage_shock_0.15pct", {"fee_pct": 0.0006, "slippage_pct": 0.0015}),
    ]

    candidate_overrides = {
        "adx_chop_threshold": 26.0,
        "bb_std": 1.8,
        "stat_arb_entry_z": 2.5,
        "stat_arb_min_edge_cost_ratio": 2.5,
        "pairs_universe": [
            ["NVDA", "AAPL"],
            ["WULF", "CIFR"],
            ["IREN", "CIFR"],
            ["BNB/USDT", "LINK/USDT"],
            ["SOL/USDT", "LINK/USDT"],
            ["MU", "MCHP"],
        ],
    }

    portfolio_results = {
        "version_b_baseline": {},
        "version_a_active": {},
        "version_a_candidate": {},
    }

    print("\n--- PHASE 4: PORTFOLIO-LEVEL COST STRESS REPLAYS ---")
    for s_name, s_costs in scenarios:
        print(f"\nExecuting scenario: {s_name} (fee={s_costs['fee_pct']}, slip={s_costs['slippage_pct']})")

        # Version B (Git baseline 861f98e)
        res_b = run_worker(BASELINE_ROOT, {"cost_scenario": s_costs, "overrides": {}})
        portfolio_results["version_b_baseline"][s_name] = res_b
        print(f"  Version B (Baseline): Net={res_b['net_return_pct']:+6.2f}% | Sharpe={res_b['sharpe_ratio']:4.2f} | PnL=${res_b['net_pnl']:8.2f} | Fees=${res_b['total_fees_paid']:6.2f} | Trades={res_b['directional_trades']}")

        # Version A Active (working tree code + active config)
        res_a_act = run_worker(PROJECT_ROOT, {"cost_scenario": s_costs, "overrides": {}})
        portfolio_results["version_a_active"][s_name] = res_a_act
        print(f"  Version A (Active):   Net={res_a_act['net_return_pct']:+6.2f}% | Sharpe={res_a_act['sharpe_ratio']:4.2f} | PnL=${res_a_act['net_pnl']:8.2f} | Fees=${res_a_act['total_fees_paid']:6.2f} | Trades={res_a_act['directional_trades']}")

        # Version A Candidate (working tree code + PROP-ED02E10E)
        res_a_cand = run_worker(PROJECT_ROOT, {"cost_scenario": s_costs, "overrides": candidate_overrides})
        portfolio_results["version_a_candidate"][s_name] = res_a_cand
        print(f"  Version A (Candidate):Net={res_a_cand['net_return_pct']:+6.2f}% | Sharpe={res_a_cand['sharpe_ratio']:4.2f} | PnL=${res_a_cand['net_pnl']:8.2f} | Fees=${res_a_cand['total_fees_paid']:6.2f} | Trades={res_a_cand['directional_trades']}")

    print("\n--- PHASE 5: STRATEGY-BY-STRATEGY COMPARISON ---")
    strategies = ["momentum", "mean_reversion", "stat_arb_pairs", "basis_yield"]
    strategy_results = {
        "version_b_baseline": {},
        "version_a_active": {},
        "version_a_candidate": {},
    }

    standard_cost = {"fee_pct": 0.0006, "slippage_pct": 0.0004}
    for strat in strategies:
        print(f"Executing standalone strategy: {strat}")
        strat_b = run_worker(BASELINE_ROOT, {"cost_scenario": standard_cost, "strategy_filter": strat})
        strategy_results["version_b_baseline"][strat] = strat_b

        strat_a_act = run_worker(PROJECT_ROOT, {"cost_scenario": standard_cost, "strategy_filter": strat})
        strategy_results["version_a_active"][strat] = strat_a_act

        strat_a_cand = run_worker(PROJECT_ROOT, {"cost_scenario": standard_cost, "strategy_filter": strat, "overrides": candidate_overrides})
        strategy_results["version_a_candidate"][strat] = strat_a_cand

        print(f"  {strat:15} | Ver B: Net={strat_b['net_return_pct']:+6.2f}% | Ver A Active: Net={strat_a_act['net_return_pct']:+6.2f}% | Ver A Cand: Net={strat_a_cand['net_return_pct']:+6.2f}%")

    print("\n--- PHASE 7: ENGINEERING BENCHMARKS ---")
    eng_benchmarks = benchmark_engineering_components()
    print("Engineering benchmarks complete:")
    print(json.dumps(eng_benchmarks, indent=2))

    comparison_results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_state": {
            "version_a_head": "9d9d7b16740d0d18c3988fe607c45d4f2e2dd2c4",
            "version_b_baseline": "861f98e56b3e695d3be9e3ca2d9ae69ec90cf58d",
            "branch": "optimize/web4-profitability-engineering",
        },
        "portfolio_stress_results": portfolio_results,
        "engineering_benchmarks": eng_benchmarks,
    }

    comp_path = OUT_DIR / "comparison_results.json"
    with open(comp_path, "w") as f:
        json.dump(comparison_results, f, indent=2)
    print(f"\nSaved portfolio results to: {comp_path}")

    strat_path = OUT_DIR / "strategy_comparison.json"
    with open(strat_path, "w") as f:
        json.dump(strategy_results, f, indent=2)
    print(f"Saved strategy results to: {strat_path}")


if __name__ == "__main__":
    main()
