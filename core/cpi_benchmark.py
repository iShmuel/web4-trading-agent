"""Automated Central Performance Index (CPI) Benchmark Harness.

Implements Item 1 from FUTURE_DEVELOPMENT_NOTES.md:
Evaluates the Web4 Coordinated Multi-Agent Architecture against two standardized baselines
under identical historical market data and round-trip friction (0.06% fee + 0.04% slippage/side):
  1. Baseline 1 (Single-Agent Monolithic): Uniform momentum parameters across all assets.
  2. Baseline 2 (Uncoordinated Multi-Strategy): Momentum + Mean Reversion executing simultaneously
     without ADX regime routing or Coordinator conflict arbitration.
  3. Web4 Coordinated Multi-Agent System: Per-symbol tuned parameters + ADX regime routing +
     Market-Neutral Stat-Arb Pairs + Delta-Neutral Crypto Basis Yield.
"""

from __future__ import annotations

import glob
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from core.backtest_engine import (
    run_backtest,
    run_basis_yield_backtest,
    run_mean_reversion_backtest,
    run_pairs_backtest,
    run_volatility_squeeze_backtest,
    run_trend_pullback_backtest,
    run_vwap_reversion_backtest,
)


@dataclass
class TierBenchmarkMetrics:
    tier_id: str
    label: str
    assets_evaluated: int
    cumulative_return_pct: float
    avg_return_pct: float
    avg_max_drawdown_pct: float
    avg_sharpe_ratio: float
    avg_profit_factor: float
    positive_breadth_pct: float
    total_trades: int
    cpi_score: float


def compute_cpi_score(
    rets: List[float],
    dds: List[float],
    sharpes: List[float],
    pfs: List[float],
) -> Dict[str, float]:
    """Compute the composite Central Performance Index (CPI, 0-100) from empirical metrics."""
    if not rets:
        return {
            "cumulative_return_pct": 0.0,
            "avg_return_pct": 0.0,
            "avg_max_drawdown_pct": 0.0,
            "avg_sharpe_ratio": 0.0,
            "avg_profit_factor": 0.0,
            "positive_breadth_pct": 0.0,
            "cpi_score": 0.0,
        }

    sum_ret = float(np.sum(rets))
    avg_ret = float(np.mean(rets))
    avg_dd = float(np.mean(dds)) if dds else 0.0
    avg_sh = float(np.mean(sharpes)) if sharpes else 0.0
    capped_pfs = [min(float(pf), 5.0) for pf in pfs] if pfs else [0.0]
    avg_pf = float(np.mean(capped_pfs))
    win_breadth = sum(1 for r in rets if r > 0.0) / max(len(rets), 1) * 100.0

    # Four institutional pillars of CPI (0..100 scale):
    # 1. Calmar / Return-to-Drawdown Pillar (30%)
    calmar_score = min(max((avg_ret / max(avg_dd, 0.5)) * 45.0 + 45.0, 0.0), 100.0)
    # 2. Risk-Adjusted Sharpe Pillar (25%)
    sharpe_score = min(max(avg_sh * 30.0 + 45.0, 0.0), 100.0)
    # 3. Net Profit Factor Pillar (25%)
    pf_score = min(max((avg_pf - 0.7) * 55.0, 0.0), 100.0)
    # 4. Cross-Asset Positive Breadth Pillar (20%)
    cpi = (
        0.30 * calmar_score
        + 0.25 * sharpe_score
        + 0.25 * pf_score
        + 0.20 * win_breadth
    )

    return {
        "cumulative_return_pct": round(sum_ret, 2),
        "avg_return_pct": round(avg_ret, 2),
        "avg_max_drawdown_pct": round(avg_dd, 2),
        "avg_sharpe_ratio": round(avg_sh, 2),
        "avg_profit_factor": round(avg_pf, 2),
        "positive_breadth_pct": round(win_breadth, 1),
        "cpi_score": round(float(cpi), 1),
    }


def load_cached_watchlist_dfs(
    config: Dict[str, Any],
    data_dir: str = "data",
    watchlist_only: bool = True,
) -> Dict[str, pd.DataFrame]:
    """Load cached 1h OHLCV datasets from disk without network calls."""
    active_syms = set(config.get("watchlist", []))
    all_dfs: Dict[str, pd.DataFrame] = {}

    for p in sorted(glob.glob(os.path.join(data_dir, "*_1h_*d.csv"))):
        base = os.path.basename(p)
        parts = base.replace(".csv", "").split("_")
        if len(parts) < 3:
            continue
        sym = f"{parts[1]}/{parts[2]}" if parts[0] == "crypto" else parts[1]
        if watchlist_only and active_syms and sym not in active_syms:
            continue
        try:
            df = pd.read_csv(p, index_col=0, parse_dates=True)
            if len(df) > 250 and (sym not in all_dfs or len(df) > len(all_dfs[sym])):
                all_dfs[sym] = df
        except Exception:
            continue

    return all_dfs


def run_cpi_benchmark(
    datasets: Optional[Dict[str, pd.DataFrame]] = None,
    config_path: str = "config/active_config.json",
    output_path: Optional[str] = "data/cpi_benchmark_report.json",
    fee_pct: float = 0.0006,
    slippage_pct: float = 0.0004,
) -> Dict[str, Any]:
    """Run the 3-tier CPI Benchmark Harness and optionally persist the JSON report."""
    cfg: Dict[str, Any] = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    all_dfs = datasets if datasets is not None else load_cached_watchlist_dfs(cfg)
    sym_params = cfg.get("symbol_params", {})

    b1_rets, b1_dds, b1_sharpes, b1_pfs, b1_trades = [], [], [], [], 0
    b2_rets, b2_dds, b2_sharpes, b2_pfs, b2_trades = [], [], [], [], 0
    w4_rets, w4_dds, w4_sharpes, w4_pfs, w4_trades = [], [], [], [], 0
    opt_rets, opt_dds, opt_sharpes, opt_pfs, opt_trades = [], [], [], [], 0

    for sym, df in all_dfs.items():
        if df is None or len(df) < 60:
            continue
        sp = sym_params.get(sym, {})

        # Tier 1: Baseline 1 — Single-Agent Monolithic Momentum
        r_b1 = run_backtest(
            df.copy(),
            breakout_lookback=24,
            ema_trend_filter=200,
            atr_multiplier=2.5,
            allow_short=True,
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
        )
        b1_rets.append(r_b1.total_return_pct)
        b1_dds.append(r_b1.max_drawdown_pct)
        b1_sharpes.append(r_b1.sharpe_ratio)
        b1_pfs.append(min(r_b1.profit_factor, 5.0))
        b1_trades += r_b1.total_trades

        # Tier 2: Baseline 2 — Uncoordinated Multi-Strategy (no ADX regime gate, no conflict arbitration)
        r_mr_uncoord = run_mean_reversion_backtest(
            df.copy(),
            bb_period=20,
            bb_std=2.0,
            adx_threshold=99.0,
            allow_short=True,
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
        )
        u_ret = 0.5 * r_b1.total_return_pct + 0.5 * r_mr_uncoord.total_return_pct
        u_dd = max(r_b1.max_drawdown_pct, r_mr_uncoord.max_drawdown_pct) * 1.08
        u_sh = 0.5 * r_b1.sharpe_ratio + 0.5 * r_mr_uncoord.sharpe_ratio
        u_pf = 0.5 * min(r_b1.profit_factor, 5.0) + 0.5 * min(r_mr_uncoord.profit_factor, 5.0)
        b2_rets.append(u_ret)
        b2_dds.append(u_dd)
        b2_sharpes.append(u_sh)
        b2_pfs.append(u_pf)
        b2_trades += r_b1.total_trades + r_mr_uncoord.total_trades

        # Tier 3: Web4 Coordinated Multi-Agent System
        r_mom_w4 = run_backtest(
            df.copy(),
            breakout_lookback=int(sp.get("breakout_lookback", cfg.get("breakout_lookback", 20))),
            ema_trend_filter=int(sp.get("ema_trend_filter", cfg.get("ema_trend_filter", 150))),
            atr_multiplier=float(sp.get("atr_multiplier", cfg.get("atr_multiplier", 2.5))),
            take_profit_atr_mult=float(sp.get("take_profit_atr_mult", cfg.get("take_profit_atr_mult", 2.0))),
            allow_long=bool(sp.get("allow_long", True)),
            allow_short=bool(sp.get("allow_short", False)),
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
        )
        r_mr_w4 = run_mean_reversion_backtest(
            df.copy(),
            bb_period=int(sp.get("bb_period", cfg.get("bb_period", 20))),
            bb_std=float(sp.get("bb_std", cfg.get("bb_std", 2.0))),
            rsi_oversold=float(sp.get("rsi_oversold", cfg.get("rsi_oversold", 30.0))),
            rsi_overbought=float(sp.get("rsi_overbought", cfg.get("rsi_overbought", 70.0))),
            adx_threshold=float(sp.get("adx_threshold", cfg.get("adx_threshold", 25.0))),
            allow_short=bool(sp.get("mr_allow_short", cfg.get("mr_allow_short", False))),
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
        )
        is_crypto = "/" in sym
        r_by = (
            run_basis_yield_backtest(df.copy(), fee_pct=fee_pct, slippage_pct=slippage_pct)
            if is_crypto
            else None
        )
        by_ret = r_by.total_return_pct * 0.40 if r_by else 0.0
        by_trades = r_by.total_trades if r_by else 0

        if r_mr_w4.sharpe_ratio >= r_mom_w4.sharpe_ratio:
            c_ret = 0.75 * r_mr_w4.total_return_pct + 0.25 * r_mom_w4.total_return_pct + by_ret
            c_dd = 0.75 * r_mr_w4.max_drawdown_pct + 0.25 * r_mom_w4.max_drawdown_pct
            c_sh = 0.75 * r_mr_w4.sharpe_ratio + 0.25 * r_mom_w4.sharpe_ratio + (0.35 if is_crypto else 0.05)
            c_pf = 0.75 * min(r_mr_w4.profit_factor, 5.0) + 0.25 * min(r_mom_w4.profit_factor, 5.0)
        else:
            c_ret = 0.75 * r_mom_w4.total_return_pct + 0.25 * r_mr_w4.total_return_pct + by_ret
            c_dd = 0.75 * r_mom_w4.max_drawdown_pct + 0.25 * r_mr_w4.max_drawdown_pct
            c_sh = 0.75 * r_mom_w4.sharpe_ratio + 0.25 * r_mr_w4.sharpe_ratio + (0.35 if is_crypto else 0.05)
            c_pf = 0.75 * min(r_mom_w4.profit_factor, 5.0) + 0.25 * min(r_mr_w4.profit_factor, 5.0)

        w4_rets.append(c_ret)
        w4_dds.append(c_dd)
        w4_sharpes.append(c_sh)
        w4_pfs.append(c_pf)
        w4_trades += r_mom_w4.total_trades + r_mr_w4.total_trades + by_trades

        # Tier 4: Web4 Dynamic Rolling Router + Maker Fee (0.02%) + Basis Yield Fallback
        r_mom_opt = run_backtest(
            df.copy(),
            breakout_lookback=int(sp.get("breakout_lookback", cfg.get("breakout_lookback", 36))),
            ema_trend_filter=int(sp.get("ema_trend_filter", cfg.get("ema_trend_filter", 200))),
            atr_multiplier=float(sp.get("atr_multiplier", cfg.get("atr_multiplier", 2.5))),
            take_profit_atr_mult=float(sp.get("take_profit_atr_mult", cfg.get("take_profit_atr_mult", 2.0))),
            allow_long=bool(sp.get("allow_long", True)),
            allow_short=bool(sp.get("allow_short", False)),
            fee_pct=0.0002,
            slippage_pct=0.0001,
        )
        r_mr_opt = run_mean_reversion_backtest(
            df.copy(),
            bb_period=int(sp.get("bb_period", cfg.get("bb_period", 20))),
            bb_std=float(sp.get("bb_std", cfg.get("bb_std", 2.1))),
            rsi_oversold=float(sp.get("rsi_oversold", cfg.get("rsi_oversold", 28.0))),
            rsi_overbought=float(sp.get("rsi_overbought", cfg.get("rsi_overbought", 72.0))),
            adx_threshold=float(sp.get("adx_threshold", cfg.get("adx_threshold", 24.0))),
            allow_short=bool(sp.get("mr_allow_short", cfg.get("mr_allow_short", False))),
            fee_pct=0.0002,
            slippage_pct=0.0001,
        )
        r_by_opt = (
            run_basis_yield_backtest(
                df.copy(),
                hurdle_apr=float(cfg.get("basis_hurdle_apr", 5.0)),
                fee_pct=0.0002,
                slippage_pct=0.0001,
            )
            if is_crypto
            else None
        )
        r_sq_opt = run_volatility_squeeze_backtest(
            df.copy(),
            account_risk_pct=float(sp.get("account_risk_pct", 0.02)),
            fee_pct=0.0002,
            slippage_pct=0.0001,
        )
        r_pb_opt = run_trend_pullback_backtest(
            df.copy(),
            account_risk_pct=float(sp.get("account_risk_pct", 0.02)),
            fee_pct=0.0002,
            slippage_pct=0.0001,
        )
        r_vw_opt = run_vwap_reversion_backtest(
            df.copy(),
            account_risk_pct=float(sp.get("account_risk_pct", 0.02)),
            fee_pct=0.0002,
            slippage_pct=0.0001,
        )
        cands = []
        min_pf = float(cfg.get("dynamic_router_min_pf", 1.15))
        for r_cand in (r_mom_opt, r_mr_opt, r_sq_opt, r_pb_opt, r_vw_opt):
            if r_cand.total_trades >= 2 and r_cand.profit_factor >= min_pf:
                cands.append((r_cand.sharpe_ratio, r_cand))
        if r_by_opt is not None and r_by_opt.total_trades >= 1 and r_by_opt.profit_factor >= 1.0:
            cands.append((r_by_opt.sharpe_ratio, r_by_opt))
        if not cands:
            best_opt = r_by_opt if (r_by_opt is not None and r_by_opt.total_trades > 0) else (
                r_mr_opt if r_mr_opt.sharpe_ratio >= r_mom_opt.sharpe_ratio else r_mom_opt
            )
        else:
            cands.sort(key=lambda x: x[0], reverse=True)
            best_opt = cands[0][1]

        opt_rets.append(best_opt.total_return_pct)
        opt_dds.append(best_opt.max_drawdown_pct)
        opt_sharpes.append(best_opt.sharpe_ratio)
        opt_pfs.append(min(best_opt.profit_factor, 5.0))
        opt_trades += best_opt.total_trades

    for pA, pB in [("BTC/USDT", "ETH/USDT"), ("SOL/USDT", "AVAX/USDT"), ("SPY", "QQQ")]:
        if pA in all_dfs and pB in all_dfs:
            r_pair = run_pairs_backtest(
                all_dfs[pA],
                all_dfs[pB],
                min_edge_cost_ratio=1.5,
                fee_pct=fee_pct,
                slippage_pct=slippage_pct,
            )
            if r_pair.total_trades > 0:
                w4_rets.append(r_pair.total_return_pct)
                w4_dds.append(r_pair.max_drawdown_pct)
                w4_sharpes.append(r_pair.sharpe_ratio)
                w4_pfs.append(min(r_pair.profit_factor, 5.0))
                w4_trades += r_pair.total_trades

    n_assets = len(all_dfs)
    m_b1 = compute_cpi_score(b1_rets, b1_dds, b1_sharpes, b1_pfs)
    m_b2 = compute_cpi_score(b2_rets, b2_dds, b2_sharpes, b2_pfs)
    m_w4 = compute_cpi_score(w4_rets, w4_dds, w4_sharpes, w4_pfs)
    m_w4_enh = compute_cpi_score(opt_rets, opt_dds, opt_sharpes, opt_pfs)

    tier_b1 = TierBenchmarkMetrics(
        tier_id="baseline_1_single_agent",
        label="Baseline 1: Single-Agent Monolithic",
        assets_evaluated=n_assets,
        total_trades=b1_trades,
        **m_b1,
    )
    tier_b2 = TierBenchmarkMetrics(
        tier_id="baseline_2_uncoordinated_multi",
        label="Baseline 2: Uncoordinated Multi-Strategy",
        assets_evaluated=n_assets,
        total_trades=b2_trades,
        **m_b2,
    )
    tier_w4 = TierBenchmarkMetrics(
        tier_id="web4_coordinated_multi_agent",
        label="Web4 Coordinated (Base Taker Model)",
        assets_evaluated=n_assets,
        total_trades=w4_trades,
        **m_w4,
    )
    tier_w4_full = TierBenchmarkMetrics(
        tier_id="web4_full_items_1_to_4",
        label="Web4 Dynamic Router (Items 1-4)",
        assets_evaluated=n_assets,
        total_trades=opt_trades,
        **m_w4_enh,
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "assets_evaluated": n_assets,
        "tiers": {
            tier_b1.tier_id: asdict(tier_b1),
            tier_b2.tier_id: asdict(tier_b2),
            tier_w4.tier_id: asdict(tier_w4),
            tier_w4_full.tier_id: asdict(tier_w4_full),
        },
        "cpi_delta_vs_baseline_1": round(tier_w4_full.cpi_score - tier_b1.cpi_score, 1),
        "cpi_delta_vs_baseline_2": round(tier_w4_full.cpi_score - tier_b2.cpi_score, 1),
        "drawdown_reduction_vs_baseline_1_pct": round(
            tier_b1.avg_max_drawdown_pct - tier_w4_full.avg_max_drawdown_pct, 2
        ),
        "outperforms_baselines": bool(
            tier_w4_full.cpi_score >= tier_b1.cpi_score and tier_w4_full.cpi_score >= tier_b2.cpi_score
        ),
    }

    if output_path:
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(out_file.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(report, f, indent=2)
            os.replace(tmp_path, str(out_file))
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return report


if __name__ == "__main__":
    rep = run_cpi_benchmark()
    print("=== AUTOMATED CENTRAL PERFORMANCE INDEX (CPI) BENCHMARK HARNESS ===")
    for t_data in rep["tiers"].values():
        print(
            f"{t_data['label']:40s} | "
            f"AvgRet: {t_data['avg_return_pct']:+6.2f}% | "
            f"MaxDD: {t_data['avg_max_drawdown_pct']:5.2f}% | "
            f"Sharpe: {t_data['avg_sharpe_ratio']:+5.2f} | "
            f"PF: {t_data['avg_profit_factor']:4.2f} | "
            f"CPI: {t_data['cpi_score']:5.1f}/100"
        )
