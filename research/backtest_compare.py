"""Profitability Research & Multi-Strategy Baseline Comparison Pipeline.

Evaluates:
1. Momentum / Trend Following strategy
2. Mean Reversion strategy
3. Buy-and-Hold benchmark
Across all cached historical assets using realistic costs (fees + slippage).
Calculates net expectancy, profit factor, win rate, Sharpe ratio, drawdown, and cost drag.
"""

import json
import sys
from pathlib import Path
from typing import Dict, Any, List
import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.data_loader import fetch_ohlcv, get_data_dir
from core.backtest_engine import run_backtest, run_mean_reversion_backtest, BacktestResult
from core.coordinator import resolve_symbol_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = PROJECT_ROOT / "data" / "profitability_research.json"


def evaluate_buy_and_hold(df: pd.DataFrame, initial_capital: float = 10000.0) -> Dict[str, Any]:
    """Calculate Buy-and-Hold baseline performance over the same dataset."""
    first_close = float(df.iloc[0]["close"])
    last_close = float(df.iloc[-1]["close"])
    bh_return_pct = ((last_close - first_close) / first_close) * 100.0

    # Drawdown of buy and hold
    cum_max = df["close"].cummax()
    dd = (cum_max - df["close"]) / cum_max
    max_dd_pct = float(dd.max() * 100.0)

    # Sharpe of buy and hold
    pct_returns = df["close"].pct_change().dropna()
    mean_ret = pct_returns.mean()
    std_ret = pct_returns.std()
    bars_per_year = 8760.0  # approximate hourly
    sharpe = float(mean_ret / std_ret * np.sqrt(bars_per_year)) if std_ret > 0 else 0.0

    return {
        "initial_capital": initial_capital,
        "final_equity": initial_capital * (1.0 + bh_return_pct / 100.0),
        "return_pct": round(bh_return_pct, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "sharpe_ratio": round(sharpe, 2),
    }


def analyze_trades(result: BacktestResult) -> Dict[str, Any]:
    """Extract granular trade expectancy metrics."""
    trades = result.trades
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "total_trades": 0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "net_expectancy_dollar": 0.0,
            "net_expectancy_pct": 0.0,
            "avg_winner_dollar": 0.0,
            "avg_loser_dollar": 0.0,
            "total_fees_paid": result.total_fees_paid,
        }

    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]

    win_rate = len(wins) / total_trades
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(abs(np.mean(losses))) if losses else 0.0

    # Expectancy formula: (Win Rate * Avg Win) - (Loss Rate * Avg Loss)
    expectancy_dollar = (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss)
    avg_return_pct = float(np.mean([t.return_pct for t in trades]) * 100.0)

    return {
        "total_trades": total_trades,
        "win_rate_pct": round(win_rate * 100.0, 2),
        "profit_factor": round(result.profit_factor, 2),
        "net_expectancy_dollar": round(expectancy_dollar, 2),
        "net_expectancy_pct": round(avg_return_pct, 2),
        "avg_winner_dollar": round(avg_win, 2),
        "avg_loser_dollar": round(avg_loss, 2),
        "total_fees_paid": round(result.total_fees_paid, 2),
    }


def run_comparative_research(
    symbols: List[str] = None,
    timeframe: str = "1h",
    days: int = 180,
) -> Dict[str, Any]:
    config_path = PROJECT_ROOT / "config" / "active_config.json"
    active_config = {}
    if config_path.exists():
        with open(config_path) as f:
            active_config = json.load(f)

    if symbols is None:
        symbols = active_config.get(
            "watchlist",
            [
                "BTC/USDT", "ETH/USDT", "SOL/USDT", "SUI/USDT", "TAO/USDT", "DOGE/USDT",
                "AVAX/USDT", "BNB/USDT", "NEAR/USDT", "LINK/USDT",
                "NVDA", "AMD", "AAPL", "PLTR", "TSLA",
                "SPY", "QQQ", "GLD", "SLV", "TLT", "XLE"
            ]
        )

    print("=" * 80)
    print("      PROFITABILITY RESEARCH: INDEPENDENT STRATEGY BASELINE EVALUATION")
    print("=" * 80)
    print(f"Watchlist:      {', '.join(symbols)}")
    print(f"Timeframe:      {timeframe} | History: ~{days} days")
    print(f"Fee Model:      0.06% exchange fee + 0.04% slippage (round-trip 0.20%) | Fill Mode: next_open")
    print("-" * 80)

    full_results: Dict[str, Any] = {}

    for sym in symbols:
        try:
            sym_cfg = resolve_symbol_config(active_config, sym) if active_config else {}
            df = fetch_ohlcv(symbol=sym, timeframe=timeframe, days=days)
        except Exception as e:
            print(f"⚠️ Skipping {sym}: failed to load ({e})")
            continue

        print(f"\nEvaluating: {sym} ({len(df)} candles)")

        # 1. Buy and Hold
        bh = evaluate_buy_and_hold(df)

        # 2. Momentum Strategy (with per-asset profile parameters)
        mom_res = run_backtest(
            df=df,
            initial_capital=10000.0,
            account_risk_pct=sym_cfg.get("account_risk_pct", 0.02),
            breakout_lookback=sym_cfg.get("breakout_lookback", 24),
            ema_trend_filter=sym_cfg.get("ema_trend_filter", 50 if "/" in sym else 100),
            atr_period=sym_cfg.get("atr_period", 14),
            atr_multiplier=sym_cfg.get("atr_multiplier", 2.5),
            take_profit_atr_mult=sym_cfg.get("take_profit_atr_mult", 2.5),
            take_profit_ratio=sym_cfg.get("take_profit_ratio", 0.5),
            allow_short=sym_cfg.get("allow_short", False),
            fee_pct=sym_cfg.get("fee_pct", 0.0006),
            slippage_pct=sym_cfg.get("slippage_pct", 0.0004),
            fill_mode="next_open",
        )
        mom_metrics = analyze_trades(mom_res)

        # 3. Mean Reversion Strategy
        mr_res = run_mean_reversion_backtest(
            df=df,
            initial_capital=10000.0,
            account_risk_pct=0.02,
            bb_period=20,
            bb_std=2.0,
            rsi_period=14,
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            atr_period=14,
            atr_stop_mult=2.0,
            adx_period=14,
            adx_threshold=25.0,
            max_hold_bars=48,
            allow_short=False,
            fee_pct=0.0006,
            slippage_pct=0.0004,
            fill_mode="next_open",
        )
        mr_metrics = analyze_trades(mr_res)

        # Print asset summary
        print(f"  {'METRIC':<24} {'BUY & HOLD':<16} {'MOMENTUM':<18} {'MEAN REVERSION':<18}")
        print("  " + "-" * 76)
        print(f"  {'Total Return %':<24} {bh['return_pct']:>+10.2f}%    {mom_res.total_return_pct:>+12.2f}%    {mr_res.total_return_pct:>+12.2f}%")
        print(f"  {'Max Drawdown %':<24} {bh['max_drawdown_pct']:>10.2f}%    {mom_res.max_drawdown_pct:>12.2f}%    {mr_res.max_drawdown_pct:>12.2f}%")
        print(f"  {'Sharpe Ratio':<24} {bh['sharpe_ratio']:>10.2f}     {mom_res.sharpe_ratio:>12.2f}     {mr_res.sharpe_ratio:>12.2f}")
        print(f"  {'Completed Trades':<24} {'N/A':>10}     {mom_metrics['total_trades']:>12}     {mr_metrics['total_trades']:>12}")
        print(f"  {'Win Rate %':<24} {'N/A':>10}     {mom_metrics['win_rate_pct']:>11.1f}%    {mr_metrics['win_rate_pct']:>11.1f}%")
        print(f"  {'Profit Factor':<24} {'N/A':>10}     {mom_metrics['profit_factor']:>12.2f}     {mr_metrics['profit_factor']:>12.2f}")
        print(f"  {'Expectancy / Trade':<24} {'N/A':>10}     ${mom_metrics['net_expectancy_dollar']:>+11.2f}     ${mr_metrics['net_expectancy_dollar']:>+11.2f}")
        print(f"  {'Total Fees Paid':<24} {'$0.00':>10}     ${mom_metrics['total_fees_paid']:>11.2f}     ${mr_metrics['total_fees_paid']:>11.2f}")

        full_results[sym] = {
            "bars": len(df),
            "buy_and_hold": bh,
            "momentum": {
                "return_pct": round(mom_res.total_return_pct, 2),
                "max_drawdown_pct": round(mom_res.max_drawdown_pct, 2),
                "sharpe_ratio": round(mom_res.sharpe_ratio, 2),
                "final_equity": round(mom_res.final_equity, 2),
                **mom_metrics,
            },
            "mean_reversion": {
                "return_pct": round(mr_res.total_return_pct, 2),
                "max_drawdown_pct": round(mr_res.max_drawdown_pct, 2),
                "sharpe_ratio": round(mr_res.sharpe_ratio, 2),
                "final_equity": round(mr_res.final_equity, 2),
                **mr_metrics,
            },
        }

    # Save to JSON
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(full_results, f, indent=2)

    print("\n" + "=" * 80)
    print(f"Research evaluation complete. Persisted results to {OUTPUT_FILE.name}")
    print("=" * 80)
    return full_results


if __name__ == "__main__":
    run_comparative_research()
