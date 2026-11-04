"""Overall Portfolio Backtest of the CURRENTLY DEPLOYED configuration.

Runs every watchlist asset through both strategy engines using the exact
per-asset profile parameters from ``config/active_config.json``:

* Momentum   -> profile breakout/EMA/ATR/TP params, ``allow_short`` from profile
* Mean Rev.  -> ``mr_allow_short`` + ``rsi_overbought`` from profile

Each asset/strategy sleeve gets an independent $10,000 paper allocation.
All trades are then pooled to produce portfolio-level metrics (net return,
profit factor, win rate, expectancy, Sharpe, max drawdown, fees) and compared
against (a) the previous long-only MR configuration and (b) buy-and-hold.

Cost model: 0.06% fee + 0.04% slippage per side, next-open execution.

NOTE: the standalone engines do NOT replay every live coordinator gate
(200-EMA macro gate, multi-timeframe confluence, LLM pre-flight, VIX /
Polymarket radars, 5-position cap). Results are therefore an approximation
of the live bot, not an exact replay.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.backtest_engine import BacktestResult, run_backtest, run_mean_reversion_backtest
from core.coordinator import resolve_symbol_config
from core.data_loader import fetch_ohlcv

CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
OUTPUT_FILE = PROJECT_ROOT / "data" / "overall_backtest_results.json"
SLEEVE_CAPITAL = 10_000.0
DAYS = 180
FEE = 0.0006
SLIP = 0.0004


def _momentum(df: pd.DataFrame, cfg: Dict[str, Any], risk: float) -> BacktestResult:
    return run_backtest(
        df=df,
        initial_capital=SLEEVE_CAPITAL,
        account_risk_pct=risk,
        breakout_lookback=cfg.get("breakout_lookback", 24),
        ema_trend_filter=cfg.get("ema_trend_filter", 50),
        atr_period=cfg.get("atr_period", 14),
        atr_multiplier=cfg.get("atr_multiplier", 2.5),
        take_profit_atr_mult=cfg.get("take_profit_atr_mult", 2.5),
        take_profit_ratio=cfg.get("take_profit_ratio", 0.5),
        allow_short=cfg.get("allow_short", False),
        fee_pct=FEE,
        slippage_pct=SLIP,
        fill_mode="next_open",
    )


def _mean_reversion(df: pd.DataFrame, cfg: Dict[str, Any], risk: float, allow_short: bool) -> BacktestResult:
    return run_mean_reversion_backtest(
        df=df,
        initial_capital=SLEEVE_CAPITAL,
        account_risk_pct=risk,
        rsi_oversold=cfg.get("rsi_oversold", 30.0),
        rsi_overbought=cfg.get("rsi_overbought", 70.0),
        adx_threshold=cfg.get("adx_threshold", 25.0),
        allow_short=allow_short,
        fee_pct=FEE,
        slippage_pct=SLIP,
        fill_mode="next_open",
    )


def _pool_metrics(trades: List[Any], capital: float, fees: float) -> Dict[str, Any]:
    pnls = np.array([t.pnl for t in trades], dtype=float)
    if len(pnls) == 0:
        return {"trades": 0}
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    gross_win, gross_loss = wins.sum(), abs(losses.sum())

    # Portfolio equity curve: realized PnL ordered by exit time on pooled capital
    ordered = sorted(trades, key=lambda t: pd.Timestamp(t.exit_time))
    curve = capital + np.cumsum([t.pnl for t in ordered])
    curve = np.concatenate([[capital], curve])
    peak = np.maximum.accumulate(curve)
    max_dd = float(((peak - curve) / peak).max() * 100.0)

    # Daily Sharpe from realized PnL
    daily = pd.Series([t.pnl for t in ordered], index=[pd.Timestamp(t.exit_time) for t in ordered])
    daily = daily.groupby(daily.index.normalize()).sum() / capital
    sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if len(daily) > 1 and daily.std() > 0 else 0.0

    return {
        "trades": int(len(pnls)),
        "net_pnl": round(float(pnls.sum()), 2),
        "net_return_pct": round(float(pnls.sum() / capital * 100.0), 2),
        "win_rate_pct": round(len(wins) / len(pnls) * 100.0, 1),
        "profit_factor": round(float(gross_win / gross_loss), 2) if gross_loss > 0 else float("inf"),
        "avg_win": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "payoff_ratio": round(float(wins.mean() / abs(losses.mean())), 2) if len(wins) and len(losses) else 0.0,
        "expectancy_per_trade": round(float(pnls.mean()), 2),
        "largest_win": round(float(pnls.max()), 2),
        "largest_loss": round(float(pnls.min()), 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_daily_ann": round(sharpe, 2),
        "fees_paid": round(fees, 2),
        "long_trades": sum(1 for t in trades if t.side == "LONG"),
        "short_trades": sum(1 for t in trades if t.side == "SHORT"),
        "short_pnl": round(float(sum(t.pnl for t in trades if t.side == "SHORT")), 2),
    }


def run() -> Dict[str, Any]:
    cfg = json.loads(CONFIG_PATH.read_text())
    watchlist = cfg["watchlist"]
    risk_weights = cfg.get("symbol_risk_weights", {})

    rows, deployed_trades, baseline_trades = [], [], []
    deployed_fees = baseline_fees = 0.0
    bh_returns = []

    print("=" * 108)
    print(f"  OVERALL BACKTEST - DEPLOYED CONFIG | {len(watchlist)} assets | ~{DAYS}d 1h | 0.06% fee + 0.04% slip | next-open")
    print("=" * 108)
    print(f"{'Symbol':<10}{'Profile':<20}{'B&H %':>8}{'Mom %':>8}{'MR %':>8}{'MR(LO) %':>10}{'Trades':>8}{'Shorts':>8}{'PF':>7}{'Win%':>7}{'DD%':>7}")
    print("-" * 108)

    for sym in watchlist:
        try:
            df = fetch_ohlcv(symbol=sym, timeframe="1h", days=DAYS)
        except Exception as e:  # noqa: BLE001
            print(f"{sym:<10} SKIPPED ({e})")
            continue
        if df is None or len(df) < 250:
            print(f"{sym:<10} SKIPPED (insufficient bars)")
            continue

        scfg = resolve_symbol_config(cfg, sym)
        risk = float(risk_weights.get(sym, cfg.get("account_risk_pct", 0.02)))
        risk = max(0.01, min(0.05, risk))  # invariant: 1%-5% hard clamp

        mom = _momentum(df, scfg, risk)
        mr = _mean_reversion(df, scfg, risk, allow_short=bool(scfg.get("mr_allow_short", False)))
        mr_lo = _mean_reversion(df, scfg, risk, allow_short=False)

        for t in mom.trades:
            t.strategy_id = "momentum"
        for t in mr.trades:
            t.strategy_id = "mean_reversion"
        sym_trades = mom.trades + mr.trades
        deployed_trades += sym_trades
        baseline_trades += mom.trades + mr_lo.trades
        deployed_fees += mom.total_fees_paid + mr.total_fees_paid
        baseline_fees += mom.total_fees_paid + mr_lo.total_fees_paid

        bh = (float(df["close"].iloc[-1]) / float(df["close"].iloc[0]) - 1.0) * 100.0
        bh_returns.append(bh)
        m = _pool_metrics(sym_trades, 2 * SLEEVE_CAPITAL, mom.total_fees_paid + mr.total_fees_paid)
        rows.append({
            "symbol": sym, "profile": scfg.get("profile_name"), "bars": len(df), "buy_hold_pct": round(bh, 2),
            "momentum_pct": round(mom.total_return_pct, 2), "mean_rev_pct": round(mr.total_return_pct, 2),
            "mean_rev_long_only_pct": round(mr_lo.total_return_pct, 2),
            "mom_dd": round(mom.max_drawdown_pct, 2), "mr_dd": round(mr.max_drawdown_pct, 2), **m,
        })
        pf = m.get("profit_factor", 0.0)
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"{sym:<10}{scfg.get('profile_name',''):<20}{bh:>+8.2f}{mom.total_return_pct:>+8.2f}{mr.total_return_pct:>+8.2f}"
              f"{mr_lo.total_return_pct:>+10.2f}{m.get('trades',0):>8}{m.get('short_trades',0):>8}{pf_s:>7}"
              f"{m.get('win_rate_pct',0):>7.1f}{max(mom.max_drawdown_pct, mr.max_drawdown_pct):>7.2f}")

    n_sleeves = 2 * len(rows)
    capital = n_sleeves * SLEEVE_CAPITAL
    deployed = _pool_metrics(deployed_trades, capital, deployed_fees)
    baseline = _pool_metrics(baseline_trades, capital, baseline_fees)
    mom_only = _pool_metrics([t for t in deployed_trades if t.strategy_id == "momentum"], len(rows) * SLEEVE_CAPITAL,
                             sum(r.get("fees_paid", 0) for r in rows) * 0)
    mr_only = _pool_metrics([t for t in deployed_trades if t.strategy_id == "mean_reversion"], len(rows) * SLEEVE_CAPITAL, 0.0)

    by_profile: Dict[str, Any] = {}
    for prof in sorted({r["profile"] for r in rows}):
        syms = {r["symbol"] for r in rows if r["profile"] == prof}
        # trades don't carry symbol; recompute from rows
        pr = [r for r in rows if r["profile"] == prof]
        by_profile[prof] = {
            "assets": len(pr),
            "net_pnl": round(sum(r.get("net_pnl", 0) for r in pr), 2),
            "avg_buy_hold_pct": round(float(np.mean([r["buy_hold_pct"] for r in pr])), 2),
            "symbols": sorted(syms),
        }

    summary = {
        "capital_pooled": capital,
        "sleeves": n_sleeves,
        "deployed": deployed,
        "baseline_long_only_mr": baseline,
        "momentum_component": mom_only,
        "mean_reversion_component": mr_only,
        "avg_buy_hold_pct": round(float(np.mean(bh_returns)), 2) if bh_returns else 0.0,
        "by_profile": by_profile,
    }

    print("=" * 108)
    print(f"PORTFOLIO (pooled ${capital:,.0f} across {n_sleeves} sleeves)")
    for name, m in [("DEPLOYED (selective shorts)", deployed), ("BASELINE (long-only MR)", baseline),
                    ("  Momentum component", mom_only), ("  Mean-Rev component", mr_only)]:
        if not m.get("trades"):
            print(f"  {name:<30} no trades")
            continue
        print(f"  {name:<30} Net ${m['net_pnl']:>+10,.2f} ({m['net_return_pct']:>+6.2f}%) | PF {m['profit_factor']:.2f} | "
              f"Win {m['win_rate_pct']:.1f}% | Trades {m['trades']} (S {m['short_trades']}) | Exp ${m['expectancy_per_trade']:+.2f} | "
              f"DD {m['max_drawdown_pct']:.2f}% | Sharpe {m['sharpe_daily_ann']:.2f}")
    print(f"  Avg Buy & Hold across assets: {summary['avg_buy_hold_pct']:+.2f}%")
    print("By profile:")
    for prof, p in by_profile.items():
        print(f"  {prof:<20} assets {p['assets']:>2} | net ${p['net_pnl']:>+9,.2f} | avg B&H {p['avg_buy_hold_pct']:+.2f}%")
    print("=" * 108)

    OUTPUT_FILE.write_text(json.dumps({"summary": summary, "assets": rows}, indent=2, default=str))
    print(f"Saved -> {OUTPUT_FILE.relative_to(PROJECT_ROOT)}")
    return summary


if __name__ == "__main__":
    run()
