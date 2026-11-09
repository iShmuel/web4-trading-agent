"""Worker script for single-version execution."""
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
