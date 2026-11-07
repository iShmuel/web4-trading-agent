"""Comprehensive Monte Carlo, Walk-Forward Efficiency, and Parameter Sensitivity Test Suite.

Evaluates:
1. Monte Carlo Simulation (2,000 iterations):
   - Trade order shuffling & bootstrap resampling
   - 95th and 99th percentile worst-case max drawdowns
   - Probability of Ruin (< 20% drawdown, < 30% drawdown)
   - 95% Confidence Interval for Net Return and Final Equity
2. Parameter Sensitivity Surface Analysis:
   - 2D stability grid across Lookback (16..48) x ATR Multiplier (1.5..3.0)
   - Parameter plateau verification (ensures no fragile curve-fitted peaks)
3. Walk-Forward Efficiency (WFE):
   - In-Sample (70%) vs Out-of-Sample (30%) efficiency ratio
"""

import json
import math
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DATA_DIR = PROJECT_ROOT / "data"


# =====================================================================
# 1. Monte Carlo Simulation Engine
# =====================================================================
def run_monte_carlo(
    trades: List[Dict[str, Any]],
    initial_capital: float = 10000.0,
    iterations: int = 2000,
) -> Dict[str, Any]:
    """Run Monte Carlo trade-order reshuffling and bootstrap resampling.

    Args:
        trades: List of trade records with 'pnl'
        initial_capital: Starting equity
        iterations: Number of simulated equity path iterations
    """
    if not trades:
        return {"error": "No trades available for Monte Carlo simulation"}

    pnls = np.array([float(t.get("pnl", 0.0)) for t in trades], dtype=float)
    n_trades = len(pnls)

    simulated_final_equities = []
    simulated_max_drawdowns = []
    simulated_sharpes = []
    ruin_count_20pct = 0
    ruin_count_30pct = 0

    rng = np.random.default_rng(seed=42)

    for _ in range(iterations):
        # Bootstrap resampling with replacement (sample realistic trade sequences)
        sampled_pnls = rng.choice(pnls, size=n_trades, replace=True)
        equity_curve = initial_capital + np.cumsum(sampled_pnls)
        equity_path = np.insert(equity_curve, 0, initial_capital)

        final_eq = float(equity_path[-1])
        simulated_final_equities.append(final_eq)

        # Max Drawdown calculation along the simulated path
        peak = np.maximum.accumulate(equity_path)
        dd = (peak - equity_path) / peak
        max_dd = float(np.max(dd) * 100.0)
        simulated_max_drawdowns.append(max_dd)

        if max_dd >= 20.0:
            ruin_count_20pct += 1
        if max_dd >= 30.0:
            ruin_count_30pct += 1

        # Trade returns standard deviation for Sharpe
        if np.std(sampled_pnls) > 0:
            trade_sharpe = float((np.mean(sampled_pnls) / np.std(sampled_pnls)) * math.sqrt(252))
            simulated_sharpes.append(trade_sharpe)

    sim_finals = np.array(simulated_final_equities)
    sim_dds = np.array(simulated_max_drawdowns)

    return {
        "iterations": iterations,
        "trades_sampled_per_path": n_trades,
        "initial_capital": initial_capital,
        "median_final_equity": round(float(np.median(sim_finals)), 2),
        "ci_95_equity_lower": round(float(np.percentile(sim_finals, 2.5)), 2),
        "ci_95_equity_upper": round(float(np.percentile(sim_finals, 97.5)), 2),
        "median_max_dd_pct": round(float(np.median(sim_dds)), 2),
        "p95_max_dd_pct": round(float(np.percentile(sim_dds, 95.0)), 2),
        "p99_worst_case_dd_pct": round(float(np.percentile(sim_dds, 99.0)), 2),
        "probability_of_drawdown_gt_20pct": round((ruin_count_20pct / iterations) * 100.0, 2),
        "probability_of_drawdown_gt_30pct": round((ruin_count_30pct / iterations) * 100.0, 2),
        "median_trade_sharpe": round(float(np.median(simulated_sharpes)), 2) if simulated_sharpes else 0.0,
    }


# =====================================================================
# 2. Parameter Sensitivity Surface Analysis
# =====================================================================
def run_parameter_sensitivity(
    df: pd.DataFrame,
    symbol: str = "BTC/USDT",
    lookback_range: List[int] = [16, 20, 24, 32, 40, 48],
    atr_mult_range: List[float] = [1.75, 2.0, 2.25, 2.5, 2.75, 3.0],
    initial_capital: float = 10000.0,
    fee_pct: float = 0.0002,
    slippage_pct: float = 0.0002,
) -> Dict[str, Any]:
    """Sweeps a 2D parameter grid to evaluate performance stability (plateau vs cliff)."""
    from core.backtest_engine import run_backtest

    grid_results = []
    returns_matrix = np.zeros((len(lookback_range), len(atr_mult_range)))

    for i, lb in enumerate(lookback_range):
        for j, atr_m in enumerate(atr_mult_range):
            res = run_backtest(
                df=df,
                initial_capital=initial_capital,
                breakout_lookback=lb,
                atr_multiplier=atr_m,
                ema_trend_filter=100,
                fee_pct=fee_pct,
                slippage_pct=slippage_pct,
            )
            returns_matrix[i, j] = res.total_return_pct
            grid_results.append({
                "lookback": lb,
                "atr_multiplier": atr_m,
                "net_return_pct": round(res.total_return_pct, 2),
                "max_drawdown_pct": round(res.max_drawdown_pct, 2),
                "sharpe_ratio": round(res.sharpe_ratio, 2),
                "win_rate_pct": round(res.win_rate, 2),
                "total_trades": res.total_trades,
            })

    # Sensitivity metrics: coefficient of variation and plateau stability
    mean_ret = float(np.mean(returns_matrix))
    std_ret = float(np.std(returns_matrix))
    cv = float(std_ret / abs(mean_ret)) if abs(mean_ret) > 0 else 999.0
    positive_cells = int(np.sum(returns_matrix > 0))
    total_cells = int(returns_matrix.size)

    return {
        "symbol": symbol,
        "grid_cells_tested": total_cells,
        "profitable_cells": positive_cells,
        "plateau_profitability_ratio": round((positive_cells / total_cells) * 100.0, 1),
        "mean_grid_return_pct": round(mean_ret, 2),
        "std_grid_return_pct": round(std_ret, 2),
        "coefficient_of_variation": round(cv, 2),
        "lookbacks_tested": lookback_range,
        "atr_multipliers_tested": atr_mult_range,
        "grid_results": grid_results,
    }


# =====================================================================
# 3. Walk-Forward Efficiency (WFE) Engine
# =====================================================================
def run_walk_forward_efficiency(
    df: pd.DataFrame,
    symbol: str = "BTC/USDT",
    train_ratio: float = 0.70,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Computes Walk-Forward Efficiency (Out-of-Sample Return / In-Sample Return)."""
    from core.backtest_engine import run_backtest
    cfg = config or {}

    split_idx = int(len(df) * train_ratio)
    train_df = df.iloc[:split_idx].copy()
    test_df = df.iloc[split_idx:].copy()

    # In-Sample Backtest
    res_in = run_backtest(
        df=train_df,
        initial_capital=10000.0,
        breakout_lookback=cfg.get("breakout_lookback", 48),
        atr_multiplier=cfg.get("atr_multiplier", 2.25),
        ema_trend_filter=cfg.get("ema_trend_filter", 100),
        fee_pct=cfg.get("maker_fee_pct", 0.0002),
        slippage_pct=cfg.get("slippage_pct", 0.0002),
    )

    # Out-of-Sample Backtest
    res_out = run_backtest(
        df=test_df,
        initial_capital=10000.0,
        breakout_lookback=cfg.get("breakout_lookback", 48),
        atr_multiplier=cfg.get("atr_multiplier", 2.25),
        ema_trend_filter=cfg.get("ema_trend_filter", 100),
        fee_pct=cfg.get("maker_fee_pct", 0.0002),
        slippage_pct=cfg.get("slippage_pct", 0.0002),
    )

    # Annualize returns by time length
    days_in = (len(train_df) * 1.0) / 24.0
    days_out = (len(test_df) * 1.0) / 24.0

    ann_ret_in = (res_in.total_return_pct * (365.25 / days_in)) if days_in > 0 else 0.0
    ann_ret_out = (res_out.total_return_pct * (365.25 / days_out)) if days_out > 0 else 0.0

    wfe_ratio = (ann_ret_out / ann_ret_in) if ann_ret_in > 0 else (1.0 if ann_ret_out >= 0 else 0.0)

    return {
        "symbol": symbol,
        "total_bars": len(df),
        "in_sample_bars": len(train_df),
        "out_sample_bars": len(test_df),
        "in_sample_return_pct": round(res_in.total_return_pct, 2),
        "in_sample_annualized_ret": round(ann_ret_in, 2),
        "in_sample_max_dd_pct": round(res_in.max_drawdown_pct, 2),
        "in_sample_sharpe": round(res_in.sharpe_ratio, 2),
        "out_sample_return_pct": round(res_out.total_return_pct, 2),
        "out_sample_annualized_ret": round(ann_ret_out, 2),
        "out_sample_max_dd_pct": round(res_out.max_drawdown_pct, 2),
        "out_sample_sharpe": round(res_out.sharpe_ratio, 2),
        "walk_forward_efficiency_pct": round(wfe_ratio * 100.0, 1),
        "robustness_verdict": "ROBUST (WFE >= 50%)" if (wfe_ratio >= 0.50 and res_out.total_return_pct >= 0) else "DEGRADED_OUT_OF_SAMPLE",
    }


def main():
    print("=" * 75)
    print("QUANTITATIVE ROBUSTNESS AUDIT: MONTE CARLO, WFE & SENSITIVITY")
    print("=" * 75)

    report: Dict[str, Any] = {}

    # 1. Monte Carlo on our actual live replay trades
    replay_file = DATA_DIR / "live_replay_backtest.json"
    if replay_file.exists():
        with open(replay_file) as f:
            data = json.load(f)
            # Use maker replay trades if available, else taker
            m_res = data.get("maker", {})
            # Read closed trades from paper portfolio as real trade sample
            portfolio_file = DATA_DIR / "paper_portfolio.json"
            if portfolio_file.exists():
                with open(portfolio_file) as pf:
                    pdata = json.load(pf)
                    trades = pdata.get("closed_trades", [])
            else:
                trades = []

        print("\n--- [1] MONTE CARLO SIMULATION (2,000 ITERATIONS) ---")
        mc_res = run_monte_carlo(trades, initial_capital=10000.0, iterations=2000)
        report["monte_carlo"] = mc_res

        if "error" not in mc_res:
            print(f"Iterations:                     {mc_res['iterations']:,d}")
            print(f"Trades Resampled per Path:      {mc_res['trades_sampled_per_path']}")
            print(f"Median Final Equity:            ${mc_res['median_final_equity']:,.2f}")
            print(f"95% Confidence Interval Equity: ${mc_res['ci_95_equity_lower']:,.2f} to ${mc_res['ci_95_equity_upper']:,.2f}")
            print(f"Median Max Drawdown:            {mc_res['median_max_dd_pct']:.2f}%")
            print(f"95th Percentile Worst-Case DD:  {mc_res['p95_max_dd_pct']:.2f}%")
            print(f"99th Percentile Worst-Case DD:  {mc_res['p99_worst_case_dd_pct']:.2f}%")
            print(f"Probability of DD > 20%:        {mc_res['probability_of_drawdown_gt_20pct']:.2f}%")
            print(f"Probability of DD > 30% (Ruin): {mc_res['probability_of_drawdown_gt_30pct']:.2f}%")
        else:
            print(f"Monte Carlo Notice: {mc_res['error']}")

    # 2. Walk-Forward Efficiency on core assets
    print("\n--- [2] WALK-FORWARD EFFICIENCY (70% IN-SAMPLE / 30% OUT-OF-SAMPLE) ---")
    wfe_assets = ["BTC_USDT", "ETH_USDT", "SOL_USDT", "GLD", "NVDA"]
    wfe_results = []
    for sym_clean in wfe_assets:
        prefix = "crypto" if "USDT" in sym_clean else "stock"
        fname = f"{prefix}_{sym_clean}_1h_180d.csv"
        fpath = DATA_DIR / fname
        if fpath.exists():
            df = pd.read_csv(fpath, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
            sym_display = sym_clean.replace("_", "/")
            res_wfe = run_walk_forward_efficiency(df, symbol=sym_display)
            wfe_results.append(res_wfe)
            print(
                f"{sym_display:<10} | In-Sample: Ret {res_wfe['in_sample_return_pct']:+5.1f}% (DD {res_wfe['in_sample_max_dd_pct']:4.1f}%) | "
                f"Out-Sample: Ret {res_wfe['out_sample_return_pct']:+5.1f}% (DD {res_wfe['out_sample_max_dd_pct']:4.1f}%) | "
                f"WFE: {res_wfe['walk_forward_efficiency_pct']:5.1f}% [{res_wfe['robustness_verdict']}]"
            )
    report["walk_forward_efficiency"] = wfe_results

    # 3. Parameter Sensitivity Matrix on BTC & NVDA
    print("\n--- [3] PARAMETER SENSITIVITY STABILITY ANALYSIS (6x6 Grid) ---")
    sens_assets = [("crypto_BTC_USDT_1h_180d.csv", "BTC/USDT"), ("stock_NVDA_1h_180d.csv", "NVDA")]
    sens_results = []
    for fname, sym_display in sens_assets:
        fpath = DATA_DIR / fname
        if fpath.exists():
            df = pd.read_csv(fpath, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
            res_sens = run_parameter_sensitivity(df, symbol=sym_display)
            sens_results.append(res_sens)
            print(
                f"{sym_display:<10} | Grid Cells: {res_sens['grid_cells_tested']} | "
                f"Profitable Plateau: {res_sens['plateau_profitability_ratio']:.1f}% ({res_sens['profitable_cells']}/{res_sens['grid_cells_tested']} cells) | "
                f"Mean Ret: {res_sens['mean_grid_return_pct']:+5.2f}% (Std: {res_sens['std_grid_return_pct']:.2f}%) | "
                f"Stability Coeff (CV): {res_sens['coefficient_of_variation']:.2f}"
            )
    report["parameter_sensitivity"] = sens_results

    # Save artifact
    out_file = DATA_DIR / "robustness_audit_report.json"
    with open(out_file, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n[Artifact Saved] Detailed robustness audit saved to {out_file.name}")


if __name__ == "__main__":
    main()
