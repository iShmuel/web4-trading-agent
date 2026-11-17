"""Institutional Quantitative Stress Testing & Robustness Suite.

Implements Tier-1 hedge fund validation standards:
1. Marcos López de Prado's Deflated Sharpe Ratio (DSR) & Probabilistic Sharpe Ratio (PSR)
2. Combinatorial Purged Cross-Validation (CPCV) with Embargoing
3. Execution Friction & Slippage Multiplier Stress Sweep (1x to 5x)
4. Underwater Drawdown Duration, Recovery Time, and Loss Streak Analysis
5. Historical Crisis Shock Replay (Flash Crash & Liquidity Freeze Stress)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations
import math
from typing import Dict, Any, List, Optional, Tuple
import numpy as np
import pandas as pd


def _norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function using math.erf (zero dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class StressSuiteReport:
    timestamp: datetime
    dsr_score: float
    psr_score: float
    is_dsr_statistically_significant: bool
    break_even_friction_multiplier: float
    friction_margin_of_safety: str
    max_underwater_duration_hours: float
    max_consecutive_losses: int
    ulcer_index: float
    calmar_ratio: float
    cpcv_folds_evaluated: int
    crisis_shock_survival: bool
    summary: str


# =====================================================================
# 1. Deflated Sharpe Ratio (DSR) & Probabilistic Sharpe Ratio (PSR)
# =====================================================================
def calculate_psr_and_dsr(
    returns: np.ndarray,
    annualization_factor: float = 252.0,
    num_trials: int = 20,
    benchmark_sr: float = 0.0,
) -> Tuple[float, float, float, Dict[str, float]]:
    """Calculates Probabilistic Sharpe Ratio (PSR) and Deflated Sharpe Ratio (DSR).

    Adjusts for track record length, fat-tailed skewness/kurtosis, and multiple testing selection bias.
    Formula per Marcos López de Prado (2014, 2018).

    Returns:
        (psr, dsr, expected_max_sr, stats_dict)
    """
    clean_rets = np.asarray(returns, dtype=float)
    clean_rets = clean_rets[~np.isnan(clean_rets)]
    t_obs = len(clean_rets)

    if t_obs < 10:
        return 0.5, 0.5, 0.0, {"error": "Insufficient return observations (<10)"}

    mean_ret = float(np.mean(clean_rets))
    std_ret = float(np.std(clean_rets, ddof=1))

    if std_ret <= 0.0:
        return 0.5, 0.5, 0.0, {"annualized_sharpe": 0.0, "skewness": 0.0, "kurtosis": 3.0}

    # Per-period Sharpe
    sr = mean_ret / std_ret
    ann_sr = sr * math.sqrt(annualization_factor)

    # Higher statistical moments: Skewness and Kurtosis
    diff = clean_rets - mean_ret
    m3 = float(np.mean(diff ** 3))
    m4 = float(np.mean(diff ** 4))
    skew = m3 / (std_ret ** 3) if std_ret > 0 else 0.0
    kurt = m4 / (std_ret ** 4) if std_ret > 0 else 3.0

    # López de Prado standard error of Sharpe ratio
    # Var(SR) = (1 - skew * SR + ((kurt - 1) / 4) * SR^2) / (T - 1)
    denom = max(1, t_obs - 1)
    sr_var = (1.0 - skew * sr + ((kurt - 1.0) / 4.0) * (sr ** 2)) / denom
    sr_std = math.sqrt(max(1e-9, sr_var))

    # Benchmark Sharpe per period
    bm_per_period = benchmark_sr / math.sqrt(annualization_factor)

    # 1. Probabilistic Sharpe Ratio (PSR)
    z_psr = (sr - bm_per_period) / sr_std
    psr = float(_norm_cdf(z_psr))

    # 2. Expected Maximum Sharpe Ratio under Null Hypothesis of N independent trials
    # E[max_N(SR)] = sqrt(2 * ln(N)) + gamma / sqrt(2 * ln(N))
    n_trials = max(1, num_trials)
    euler_mascheroni = 0.5772156649
    if n_trials > 1:
        log_n = math.log(n_trials)
        sqrt_2log_n = math.sqrt(2.0 * log_n)
        exp_max_z = (1.0 - euler_mascheroni) * (sqrt_2log_n) + euler_mascheroni * (sqrt_2log_n + 1.0 / sqrt_2log_n)
        exp_max_sr = float(exp_max_z * sr_std)
    else:
        exp_max_sr = 0.0

    # 3. Deflated Sharpe Ratio (DSR)
    z_dsr = (sr - exp_max_sr) / sr_std
    dsr = float(_norm_cdf(z_dsr))

    stats = {
        "annualized_sharpe": round(ann_sr, 2),
        "period_sharpe": round(sr, 4),
        "skewness": round(skew, 3),
        "kurtosis": round(kurt, 3),
        "observations": t_obs,
        "psr": round(psr, 4),
        "dsr": round(dsr, 4),
        "expected_max_sr_ann": round(exp_max_sr * math.sqrt(annualization_factor), 2),
    }

    return psr, dsr, exp_max_sr, stats


# =====================================================================
# 2. Combinatorial Purged Cross-Validation (CPCV) with Embargoing
# =====================================================================
def run_cpcv_split(
    df: pd.DataFrame,
    n_splits: int = 6,
    n_test_splits: int = 2,
    holding_period_bars: int = 24,
    embargo_bars: int = 48,
) -> List[Dict[str, Any]]:
    """Generates Combinatorial Purged Cross-Validation folds with Embargoing.

    Prevents serial correlation lookahead leakage across train/test boundaries.
    Per López de Prado (2018).

    Returns:
        List of fold dictionaries containing train_indices, test_indices, and split metadata.
    """
    total_bars = len(df)
    indices = np.arange(total_bars)
    chunk_size = total_bars // n_splits

    # Create n_splits chronological contiguous chunks
    chunks = []
    for i in range(n_splits):
        start = i * chunk_size
        end = (i + 1) * chunk_size if i < n_splits - 1 else total_bars
        chunks.append((start, end))

    # All combinations of n_test_splits chunks for testing
    combos = list(combinations(range(n_splits), n_test_splits))
    folds = []

    for fold_id, test_chunk_ids in enumerate(combos):
        # Build test index set
        test_idx_set = set()
        test_ranges = []
        for cid in test_chunk_ids:
            s, e = chunks[cid]
            test_idx_set.update(range(s, e))
            test_ranges.append((s, e))

        # Build train index set with Purging & Embargoing
        train_idx_list = []
        for cid in range(n_splits):
            if cid in test_chunk_ids:
                continue

            c_start, c_end = chunks[cid]
            for bar_i in range(c_start, c_end):
                # 1. Purging: Does the forward holding period of this training bar overlap with test start?
                purged = False
                for t_s, t_e in test_ranges:
                    if bar_i < t_s and (bar_i + holding_period_bars) >= t_s:
                        purged = True
                        break
                    # 2. Embargoing: Does this training bar fall in the post-test embargo buffer?
                    if t_e <= bar_i < (t_e + embargo_bars):
                        purged = True
                        break

                if not purged and bar_i not in test_idx_set:
                    train_idx_list.append(bar_i)

        test_idx_list = sorted(list(test_idx_set))
        train_idx_list = sorted(train_idx_list)

        folds.append({
            "fold_id": fold_id,
            "test_chunk_ids": list(test_chunk_ids),
            "train_indices": train_idx_list,
            "test_indices": test_idx_list,
            "train_bars": len(train_idx_list),
            "test_bars": len(test_idx_list),
        })

    return folds


# =====================================================================
# 3. Execution Friction & Slippage Multiplier Stress Sweep
# =====================================================================
def run_slippage_multiplier_stress(
    trades: List[Dict[str, Any]],
    initial_capital: float = 10000.0,
    multipliers: List[float] = [1.0, 2.0, 3.0, 5.0],
    base_fee_pct: float = 0.0002,
    base_slippage_pct: float = 0.0004,
) -> Dict[str, Any]:
    """Evaluates strategy performance across 1x to 5x fee and slippage stress tiers.

    Identifies the exact break-even friction multiplier where net profit hits $0.00.
    """
    if not trades:
        return {"error": "No trades provided for friction stress"}

    tiers = []
    x_vals = []
    y_pnls = []

    for mult in multipliers:
        stressed_capital = initial_capital
        stressed_pnls = []

        for t in trades:
            raw_pnl = float(t.get("pnl", 0.0))
            base_fee = float(t.get("fees", 0.0))
            notional = float(t.get("units", 1.0)) * float(t.get("entry_price", 100.0))

            # Additional friction under stress multiplier
            extra_fee = base_fee * (mult - 1.0)
            extra_slip = (notional * base_slippage_pct * 2.0) * (mult - 1.0)
            stressed_trade_pnl = raw_pnl - extra_fee - extra_slip
            stressed_capital += stressed_trade_pnl
            stressed_pnls.append(stressed_trade_pnl)

        net_pnl = stressed_capital - initial_capital
        net_ret = (net_pnl / initial_capital) * 100.0
        wins = sum(1 for p in stressed_pnls if p > 0)
        win_rate = (wins / len(stressed_pnls) * 100.0) if stressed_pnls else 0.0

        std_pnl = np.std(stressed_pnls)
        sharpe = (float(np.mean(stressed_pnls) / std_pnl) * math.sqrt(252)) if std_pnl > 0 else 0.0

        tiers.append({
            "multiplier": f"{mult:.1f}x",
            "net_pnl": round(net_pnl, 2),
            "net_return_pct": round(net_ret, 2),
            "win_rate_pct": round(win_rate, 2),
            "trade_sharpe": round(sharpe, 2),
        })

        x_vals.append(mult)
        y_pnls.append(net_pnl)

    # Calculate break-even friction multiplier via linear regression / root finding
    break_even_mult = 99.0
    if len(x_vals) >= 2:
        slope, intercept = np.polyfit(x_vals, y_pnls, deg=1)
        if slope < 0 and intercept > 0:
            break_even_mult = float(-intercept / slope)
        elif all(p > 0 for p in y_pnls):
            break_even_mult = 10.0
        else:
            break_even_mult = 1.0

    margin_of_safety = (
        "VERY_HIGH (Survives >5x friction)" if break_even_mult >= 5.0
        else ("HIGH (Survives 3x-5x friction)" if break_even_mult >= 3.0
        else ("ADEQUATE (Survives 2x-3x friction)" if break_even_mult >= 2.0
        else "FRAGILE (<2x fee cushion)"))
    )

    return {
        "base_capital": initial_capital,
        "total_trades_tested": len(trades),
        "break_even_multiplier": round(break_even_mult, 2),
        "friction_margin_of_safety": margin_of_safety,
        "tiers": tiers,
    }


# =====================================================================
# 4. Underwater Drawdown Duration & Streak Analysis
# =====================================================================
def calculate_underwater_and_streak_metrics(
    equity_curve: List[float],
    trade_pnls: List[float],
    hours_per_bar: float = 1.0,
) -> Dict[str, Any]:
    """Calculates time underwater, time to recovery, loss streaks, and Ulcer Index."""
    eq_arr = np.asarray(equity_curve, dtype=float)
    if len(eq_arr) < 2:
        return {"error": "Insufficient equity points"}

    peaks = np.maximum.accumulate(eq_arr)
    drawdowns = (peaks - eq_arr) / peaks
    max_dd_pct = float(np.max(drawdowns) * 100.0)

    # Underwater duration tracking
    current_underwater_bars = 0
    max_underwater_bars = 0
    recovery_times_bars = []
    in_drawdown = False
    dd_start_bar = 0

    for i, dd_val in enumerate(drawdowns):
        if dd_val > 0.0001:  # Underwater
            if not in_drawdown:
                in_drawdown = True
                dd_start_bar = i
            current_underwater_bars += 1
            if current_underwater_bars > max_underwater_bars:
                max_underwater_bars = current_underwater_bars
        else:
            if in_drawdown:
                recovery_times_bars.append(i - dd_start_bar)
                in_drawdown = False
            current_underwater_bars = 0

    max_underwater_hours = max_underwater_bars * hours_per_bar
    avg_recovery_hours = (np.mean(recovery_times_bars) * hours_per_bar) if recovery_times_bars else 0.0

    # Streak analysis
    consecutive_losses = 0
    max_consecutive_losses = 0
    consecutive_wins = 0
    max_consecutive_wins = 0

    for pnl in trade_pnls:
        if pnl <= 0.0:
            consecutive_losses += 1
            consecutive_wins = 0
            if consecutive_losses > max_consecutive_losses:
                max_consecutive_losses = consecutive_losses
        else:
            consecutive_wins += 1
            consecutive_losses = 0
            if consecutive_wins > max_consecutive_wins:
                max_consecutive_wins = consecutive_wins

    # Ulcer Index (Root-mean-square of percentage drawdowns)
    squared_dds = (drawdowns * 100.0) ** 2
    ulcer_index = float(math.sqrt(np.mean(squared_dds)))

    # Calmar Ratio = Annualized Return / Max Drawdown
    total_bars = len(eq_arr)
    days = (total_bars * hours_per_bar) / 24.0
    total_ret_pct = ((eq_arr[-1] - eq_arr[0]) / eq_arr[0]) * 100.0
    ann_ret_pct = (total_ret_pct * (365.25 / days)) if days > 0 else 0.0
    calmar = (ann_ret_pct / max_dd_pct) if max_dd_pct > 0 else 0.0

    # Sortino Ratio & CVaR (95% Expected Shortfall) on bar returns
    bar_rets = np.diff(eq_arr) / np.maximum(eq_arr[:-1], 1e-9)
    downside = bar_rets[bar_rets < 0.0]
    down_std = float(np.sqrt(np.mean(downside ** 2))) if len(downside) > 0 else 0.0
    bars_per_year = (365.25 * 24.0) / max(hours_per_bar, 1e-6)
    sortino = (float(np.mean(bar_rets)) / down_std) * math.sqrt(bars_per_year) if down_std > 1e-12 else 0.0

    cvar_95_pct = 0.0
    if len(bar_rets) >= 20:
        q5 = float(np.quantile(bar_rets, 0.05))
        tail = bar_rets[bar_rets <= q5]
        cvar_95_pct = float(np.mean(tail)) * 100.0 if len(tail) > 0 else q5 * 100.0

    return {
        "max_drawdown_pct": round(max_dd_pct, 2),
        "max_underwater_hours": round(max_underwater_hours, 1),
        "max_underwater_days": round(max_underwater_hours / 24.0, 1),
        "avg_recovery_hours": round(avg_recovery_hours, 1),
        "max_consecutive_losses": max_consecutive_losses,
        "max_consecutive_wins": max_consecutive_wins,
        "ulcer_index": round(ulcer_index, 2),
        "calmar_ratio": round(calmar, 2),
        "sortino_ratio": round(sortino, 2),
        "cvar_95_pct": round(cvar_95_pct, 3),
    }


# =====================================================================
# 5. Historical Crisis Shock Replay Engine
# =====================================================================
def run_crisis_stress_replay(
    df: pd.DataFrame,
    crisis_name: str = "Simulated Flash Crash",
    initial_capital: float = 10000.0,
    stop_loss_atr_mult: float = 2.25,
) -> Dict[str, Any]:
    """Replays bot execution through flash crashes and crisis regimes to test circuit breakers."""
    from core.backtest_engine import run_backtest

    res = run_backtest(
        df=df,
        initial_capital=initial_capital,
        breakout_lookback=24,
        atr_multiplier=stop_loss_atr_mult,
        ema_trend_filter=100,
        fee_pct=0.0006,
        slippage_pct=0.0010,  # Elevated slippage during crisis
    )

    asset_peak = float(df["close"].max())
    asset_trough = float(df["close"].min())
    asset_drop_pct = ((asset_peak - asset_trough) / asset_peak) * 100.0

    capital_preservation_ratio = 1.0 - (res.max_drawdown_pct / asset_drop_pct) if asset_drop_pct > 0 else 1.0

    return {
        "crisis_name": crisis_name,
        "underlying_asset_max_drop_pct": round(asset_drop_pct, 2),
        "portfolio_max_dd_pct": round(res.max_drawdown_pct, 2),
        "capital_preservation_ratio": round(capital_preservation_ratio * 100.0, 1),
        "circuit_breaker_triggered": res.max_drawdown_pct < asset_drop_pct,
        "final_capital": round(res.final_equity, 2),
        "net_return_pct": round(res.total_return_pct, 2),
    }
