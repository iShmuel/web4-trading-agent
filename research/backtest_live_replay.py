"""Option A: Full Live-Coordinator Historical Replay Backtest.

Replays 180 days of hourly market data candle-by-candle through the exact
live ``PortfolioCoordinator`` + ``RiskEngine`` + ``PaperAccount`` decision
pipeline with all quantitative filters active:
  - 200 EMA Macro Bear Gate
  - Multi-Timeframe Trend Anchor (min_confluence_score >= 0.70)
  - ADX Range Chop Gate (< 20) & Strong Trend MR Suppression (>= 25)
  - Bullish Liquidity Sweep-and-Reclaim
  - Order-Flow Liquidation Absorption & Surge-Volume Conviction Boost
  - Break-Even Stop Lock (1.2x ATR) & 50% Partial Scale-Out
  - Crypto Crash Contagion Cooldown (3h)
  - Relative Strength Priority Ranking (vs BTC / SPY)
  - Portfolio Risk Engine: max 5 concurrent positions, max 2 crypto positions,
    1%-5% hard risk bounds, per-symbol risk weights & exposure caps

Evaluates both:
  1. Conservative Taker + Slippage model (0.06% fee + 0.04% slippage per side)
  2. Configured Maker model (0.02% maker limit fee)
"""

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.coordinator import PortfolioCoordinator, resolve_symbol_config
from core.data_loader import fetch_ohlcv, is_crypto_symbol
from core.fred_radar import MacroRegimeReport
from core.paper_account import PaperAccount
from core.polymarket_radar import MacroEventRisk
from core.risk_engine import RiskEngine
from core.strategy import (
    calculate_relative_strength,
    compute_indicators,
    compute_mean_reversion_indicators,
)

CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
OUTPUT_FILE = PROJECT_ROOT / "data" / "live_replay_backtest.json"


def prepare_precomputed_dfs(cfg: Dict[str, Any], days: int = 180, offset_days: int = 0) -> Dict[str, pd.DataFrame]:
    """Load OHLCV and precompute strictly causal indicator columns once per symbol.
    
    If offset_days > 0, fetches (days + offset_days) of data and slices the window
    corresponding to [now - (days + offset_days) : now - offset_days].
    """
    total_days = days + offset_days
    dfs: Dict[str, pd.DataFrame] = {}
    for sym in cfg["watchlist"]:
        raw = fetch_ohlcv(sym, "1h", days=total_days)
        if raw is None or len(raw) < 250:
            continue
        scfg = resolve_symbol_config(cfg, sym)
        mom_df = compute_indicators(
            raw,
            breakout_lookback=int(scfg.get("breakout_lookback", 20)),
            ema_trend_filter=int(scfg.get("ema_trend_filter", 32)),
            atr_period=int(scfg.get("atr_period", 14)),
            macro_ema_period=int(scfg.get("macro_ema_period", 200)),
        )
        mr_df = compute_mean_reversion_indicators(
            raw,
            bb_period=int(scfg.get("bb_period", 20)),
            bb_std=float(scfg.get("bb_std", 2.0)),
            rsi_period=int(scfg.get("rsi_period", 14)),
            atr_period=int(scfg.get("atr_period", 14)),
            adx_period=int(scfg.get("adx_period", 14)),
        )
        for col in ["bb_sma", "bb_std", "bb_upper", "bb_lower", "bb_width", "z_score", "rsi"]:
            mom_df[col] = mr_df[col]
        mom_df["_precomputed"] = True

        if offset_days > 0:
            cutoff_date = mom_df.index[-1] - pd.Timedelta(days=offset_days)
            start_date = cutoff_date - pd.Timedelta(days=days)
            sliced_df = mom_df.loc[(mom_df.index >= start_date) & (mom_df.index < cutoff_date)]
            if len(sliced_df) >= 150:
                dfs[sym] = sliced_df
        else:
            dfs[sym] = mom_df
    return dfs


def run_single_replay(
    dfs: Dict[str, pd.DataFrame],
    base_cfg: Dict[str, Any],
    execution_mode: str = "taker",
    initial_capital: float = 10_000.0,
) -> Dict[str, Any]:
    cfg = json.loads(json.dumps(base_cfg))
    cfg["max_stale_minutes"] = 0
    cfg["preflight_max_staleness_seconds"] = 999999999
    cfg["enable_funding_radar"] = False
    cfg["enable_local_llm_preflight"] = False
    cfg["execution_mode"] = execution_mode

    tmp_port = Path(f"/tmp/live_replay_{execution_mode}_{int(time.time()*1000)}.json")
    if tmp_port.exists():
        tmp_port.unlink()
    if tmp_port.with_suffix(".json.bak").exists():
        tmp_port.with_suffix(".json.bak").unlink()

    account = PaperAccount(initial_capital=initial_capital, portfolio_file=tmp_port)
    account._save_state = lambda: None  # fast in-memory state during replay
    account.state = {
        "cash": initial_capital,
        "positions": {},
        "closed_trades": [],
        "initial_capital": initial_capital,
    }

    risk = RiskEngine(
        default_risk_pct=float(cfg.get("account_risk_pct", 0.02)),
        max_open_positions=int(cfg.get("max_concurrent_positions", 5)),
        max_crypto_positions=int(cfg.get("max_crypto_positions", 2)),
        symbol_risk_weights=cfg.get("symbol_risk_weights", {}),
        symbol_exposure_caps=cfg.get("symbol_exposure_caps", {}),
    )

    coord = PortfolioCoordinator(account, risk, cfg)
    coord.polymarket_radar.evaluate = lambda force_refresh=False: MacroEventRisk(
        risk_level="LOW", recommended_risk_multiplier=1.0
    )
    coord.fred_radar.evaluate = lambda force_refresh=False: MacroRegimeReport(
        regime="STABLE",
        vix_level=16.0,
        risk_scale_factor=1.0,
        allow_long_breakouts=True,
        reason="Normal market conditions",
    )

    # Build unified timeline starting after 220 bars of warmup on BTC
    btc_df = dfs["BTC/USDT"]
    timeline = btc_df.index[220:]

    # Map timestamps to integer row positions for O(1) lookup
    ts_to_pos: Dict[str, Dict[pd.Timestamp, int]] = {
        sym: {ts: idx for idx, ts in enumerate(df.index)} for sym, df in dfs.items()
    }

    current_prices: Dict[str, float] = {
        sym: float(df["close"].iloc[0]) for sym, df in dfs.items()
    }
    equity_curve: List[Dict[str, Any]] = []
    gate_counts: Counter = Counter()

    spy_df = dfs.get("SPY")

    for step_idx, ts in enumerate(timeline):
        # Determine which symbols have a bar closing at this timestamp
        active_syms = [sym for sym in dfs if ts in ts_to_pos[sym]]
        if not active_syms:
            continue

        # Sort active symbols by 24-bar Relative Strength vs BTC/SPY (just like run_cycle)
        btc_pos = ts_to_pos["BTC/USDT"].get(ts, 0)
        btc_slice = btc_df["close"].iloc[max(0, btc_pos - 25) : btc_pos + 1]
        spy_pos = ts_to_pos["SPY"].get(ts) if spy_df is not None else None
        spy_slice = (
            spy_df["close"].iloc[max(0, spy_pos - 25) : spy_pos + 1]
            if (spy_df is not None and spy_pos is not None)
            else None
        )

        def rs_priority(sym: str) -> float:
            pos = ts_to_pos[sym][ts]
            if pos < 25:
                return 0.0
            s_close = dfs[sym]["close"].iloc[pos - 24 : pos + 1]
            bench = btc_slice if is_crypto_symbol(sym) else spy_slice
            if bench is not None and len(bench) >= 24 and len(s_close) >= 24:
                return calculate_relative_strength(s_close, bench, lookback=24)
            return 0.0

        sorted_syms = sorted(active_syms, key=rs_priority, reverse=True)

        for sym in sorted_syms:
            pos = ts_to_pos[sym][ts]
            if pos < 60:
                continue
            # Pass a 60-bar slice with precomputed indicators so evaluate() & liquidity sweep work
            sub_df = dfs[sym].iloc[pos - 59 : pos + 1]
            dec = coord.process_symbol(sym, sub_df, current_prices)

            if dec.action == "REJECTED":
                r = dec.reason
                if "Macro Bear Gate" in r:
                    gate_counts["200 EMA Macro Bear Gate"] += 1
                elif "Chop Gate" in r:
                    gate_counts["ADX Range Chop Gate (<20)"] += 1
                elif "Strong trend" in r or "counter-trend MR" in r:
                    gate_counts["Strong Trend MR Suppression (ADX>=25)"] += 1
                elif "Trend Anchor" in r:
                    gate_counts["Multi-Timeframe Trend Anchor (<0.70)"] += 1
                elif "Crash Contagion Cooldown" in r:
                    gate_counts["Crypto Crash Contagion Cooldown (3h)"] += 1
                elif "Correlation Guard" in r:
                    gate_counts["Crypto Correlation Guard (Max 2 Crypto)"] += 1
                elif "Max open positions" in r or "Maximum concurrent" in r:
                    gate_counts["Max Concurrent Positions Cap (5)"] += 1
                elif "Risk check blocked" in r:
                    gate_counts[f"Risk Engine ({r})"] += 1
                elif "Pre-Flight" in r:
                    gate_counts["Pre-Flight Safety Guard"] += 1
            elif dec.action == "CONFLICT":
                gate_counts["Strategy Conflict Cancellation"] += 1

        eq = account.get_total_equity(current_prices)
        equity_curve.append({"timestamp": ts, "equity": eq, "cash": account.cash, "open": len(account.positions)})

    # Compute portfolio metrics from closed trades and equity curve
    closed = account.state.get("closed_trades", [])
    eq_series = pd.Series(
        [pt["equity"] for pt in equity_curve],
        index=pd.DatetimeIndex([pt["timestamp"] for pt in equity_curve]),
    )
    final_equity = float(eq_series.iloc[-1]) if len(eq_series) else initial_capital
    net_return_pct = ((final_equity - initial_capital) / initial_capital) * 100.0
    net_pnl = final_equity - initial_capital

    peaks = eq_series.cummax()
    dd_series = (peaks - eq_series) / peaks
    max_dd_pct = float(dd_series.max() * 100.0) if len(dd_series) else 0.0

    daily_eq = eq_series.resample("1D").last().dropna()
    daily_ret = daily_eq.pct_change().dropna()
    sharpe = (
        float(daily_ret.mean() / daily_ret.std() * np.sqrt(365.0))
        if len(daily_ret) > 1 and daily_ret.std() > 0
        else 0.0
    )

    pnls = np.array([float(t["pnl"]) for t in closed], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) else 0.0
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    win_rate = round((len(wins) / len(pnls)) * 100.0, 1) if len(pnls) else 0.0
    avg_win = round(float(wins.mean()), 2) if len(wins) else 0.0
    avg_loss = round(float(losses.mean()), 2) if len(losses) else 0.0
    payoff = round(avg_win / abs(avg_loss), 2) if avg_loss < 0 else 0.0
    expectancy = round(float(pnls.mean()), 2) if len(pnls) else 0.0
    total_fees = round(sum(float(t.get("fees_paid", 0.0)) for t in closed), 2)

    # Breakdown by strategy and side
    mom_trades = [t for t in closed if t.get("strategy_id") == "momentum"]
    mr_trades = [t for t in closed if t.get("strategy_id") == "mean_reversion"]
    short_trades = [t for t in closed if t.get("side") == "SHORT"]
    long_trades = [t for t in closed if t.get("side") == "LONG"]

    # Breakdown by symbol
    by_symbol: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0, "fees": 0.0, "shorts": 0})
    for t in closed:
        sym = t["symbol"]
        by_symbol[sym]["trades"] += 1
        if float(t["pnl"]) > 0:
            by_symbol[sym]["wins"] += 1
        by_symbol[sym]["pnl"] += float(t["pnl"])
        by_symbol[sym]["fees"] += float(t.get("fees_paid", 0.0))
        if t.get("side") == "SHORT":
            by_symbol[sym]["shorts"] += 1

    return {
        "execution_mode": execution_mode,
        "initial_capital": initial_capital,
        "final_equity": round(final_equity, 2),
        "net_pnl": round(net_pnl, 2),
        "net_return_pct": round(net_return_pct, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "sharpe_ratio": round(sharpe, 2),
        "total_trades": len(closed),
        "winning_trades": int(len(wins)),
        "losing_trades": int(len(losses)),
        "win_rate_pct": win_rate,
        "profit_factor": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff,
        "expectancy_per_trade": expectancy,
        "realized_pnl": round(float(pnls.sum()), 2) if len(pnls) else 0.0,
        "total_fees_paid": total_fees,
        "momentum_trades": len(mom_trades),
        "momentum_pnl": round(sum(float(t["pnl"]) for t in mom_trades), 2),
        "mean_reversion_trades": len(mr_trades),
        "mean_reversion_pnl": round(sum(float(t["pnl"]) for t in mr_trades), 2),
        "long_trades": len(long_trades),
        "long_pnl": round(sum(float(t["pnl"]) for t in long_trades), 2),
        "short_trades": len(short_trades),
        "short_pnl": round(sum(float(t["pnl"]) for t in short_trades), 2),
        "open_positions_at_end": len(account.positions),
        "gate_counts": dict(gate_counts.most_common()),
        "by_symbol": {
            k: {
                "trades": v["trades"],
                "win_rate_pct": round(v["wins"] / v["trades"] * 100.0, 1) if v["trades"] else 0.0,
                "pnl": round(v["pnl"], 2),
                "fees": round(v["fees"], 2),
                "shorts": v["shorts"],
            }
            for k, v in sorted(by_symbol.items(), key=lambda x: x[1]["pnl"], reverse=True)
        },
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Full Live-Coordinator Historical Replay Backtest")
    parser.add_argument("--days", type=int, default=180, help="Number of historical days to replay (default: 180)")
    parser.add_argument("--offset-days", type=int, default=0, help="Historical offset days back in time (default: 0)")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path (optional)")
    args = parser.parse_args()

    days = args.days
    offset_days = args.offset_days
    out_file = Path(args.output) if args.output else OUTPUT_FILE

    t0 = time.time()
    cfg = json.loads(CONFIG_PATH.read_text())
    print("=" * 95)
    title = f"FULL LIVE-COORDINATOR HISTORICAL REPLAY ({days} DAYS"
    if offset_days > 0:
        title += f", OFFSET {offset_days} DAYS PRIOR"
    title += f", {len(cfg.get('watchlist', []))} ASSETS, ALL FILTERS ON)"
    print(f"  {title}")
    print("=" * 95)
    dfs = prepare_precomputed_dfs(cfg, days=days, offset_days=offset_days)
    print(f"Precomputed causal indicator matrices for {len(dfs)} assets in {time.time() - t0:.1f}s")

    t1 = time.time()
    res_taker = run_single_replay(dfs, cfg, execution_mode="taker", initial_capital=10_000.0)
    print(f"Completed Taker+Slippage (0.06% fee + 0.04% slip) replay in {time.time() - t1:.1f}s")

    t2 = time.time()
    res_maker = run_single_replay(dfs, cfg, execution_mode="maker", initial_capital=10_000.0)
    print(f"Completed Maker (0.02% post-only limit) replay in {time.time() - t2:.1f}s")

    print("\n" + "=" * 95)
    print(f"{'METRIC':<34} {'TAKER + SLIPPAGE (0.10%/side)':<30} {'MAKER LIMIT (0.02%/side)':<28}")
    print("-" * 95)
    rows = [
        ("Initial Capital", f"${res_taker['initial_capital']:,.2f}", f"${res_maker['initial_capital']:,.2f}"),
        ("Final Portfolio Equity", f"${res_taker['final_equity']:,.2f}", f"${res_maker['final_equity']:,.2f}"),
        ("Net Return %", f"{res_taker['net_return_pct']:+.2f}% (${res_taker['net_pnl']:+,.2f})", f"{res_maker['net_return_pct']:+.2f}% (${res_maker['net_pnl']:+,.2f})"),
        ("Realized Closed-Trade P&L", f"${res_taker['realized_pnl']:+,.2f}", f"${res_maker['realized_pnl']:+,.2f}"),
        ("Profit Factor", f"{res_taker['profit_factor']:.2f}", f"{res_maker['profit_factor']:.2f}"),
        ("Win Rate %", f"{res_taker['win_rate_pct']:.1f}% ({res_taker['winning_trades']}W / {res_taker['losing_trades']}L)", f"{res_maker['win_rate_pct']:.1f}% ({res_maker['winning_trades']}W / {res_maker['losing_trades']}L)"),
        ("Total Executions (Closures)", f"{res_taker['total_trades']}", f"{res_maker['total_trades']}"),
        ("  - Momentum Trades / P&L", f"{res_taker['momentum_trades']} (${res_taker['momentum_pnl']:+,.2f})", f"{res_maker['momentum_trades']} (${res_maker['momentum_pnl']:+,.2f})"),
        ("  - Mean-Rev Trades / P&L", f"{res_taker['mean_reversion_trades']} (${res_taker['mean_reversion_pnl']:+,.2f})", f"{res_maker['mean_reversion_trades']} (${res_maker['mean_reversion_pnl']:+,.2f})"),
        ("  - Long Trades / P&L", f"{res_taker['long_trades']} (${res_taker['long_pnl']:+,.2f})", f"{res_maker['long_trades']} (${res_maker['long_pnl']:+,.2f})"),
        ("  - Short Trades / P&L", f"{res_taker['short_trades']} (${res_taker['short_pnl']:+,.2f})", f"{res_maker['short_trades']} (${res_maker['short_pnl']:+,.2f})"),
        ("Avg Win / Avg Loss", f"${res_taker['avg_win']:+.2f} / ${res_taker['avg_loss']:+.2f} ({res_taker['payoff_ratio']:.2f}x)", f"${res_maker['avg_win']:+.2f} / ${res_maker['avg_loss']:+.2f} ({res_maker['payoff_ratio']:.2f}x)"),
        ("Expectancy per Trade", f"${res_taker['expectancy_per_trade']:+.2f}", f"${res_maker['expectancy_per_trade']:+.2f}"),
        ("Max Portfolio Drawdown %", f"{res_taker['max_drawdown_pct']:.2f}%", f"{res_maker['max_drawdown_pct']:.2f}%"),
        ("Annualized Sharpe Ratio", f"{res_taker['sharpe_ratio']:+.2f}", f"{res_maker['sharpe_ratio']:+.2f}"),
        ("Total Fees Paid", f"${res_taker['total_fees_paid']:,.2f}", f"${res_maker['total_fees_paid']:,.2f}"),
    ]
    for label, v1, v2 in rows:
        print(f"{label:<34} {v1:<30} {v2:<28}")

    print("\n" + "=" * 95)
    print("PER-ASSET CLOSED P&L BREAKDOWN (TAKER + SLIPPAGE REPLAY)")
    print("-" * 95)
    print(f"{'Symbol':<12}{'Trades':>8}{'Shorts':>8}{'Win Rate':>12}{'Fees Paid':>14}{'Net Realized P&L':>20}")
    print("-" * 95)
    for sym, stats in res_taker["by_symbol"].items():
        print(f"{sym:<12}{stats['trades']:>8}{stats['shorts']:>8}{stats['win_rate_pct']:>11.1f}%   ${stats['fees']:>10.2f}     ${stats['pnl']:>+14.2f}")

    print("\n" + "=" * 95)
    print("DEFENSE GATES TRIGGERED ACROSS PERIOD (CANDLE EVALUATIONS BLOCKED)")
    print("-" * 95)
    for gate, cnt in res_taker["gate_counts"].items():
        print(f"  {gate:<48} {cnt:>8,d} times")
    print("=" * 95)

    out_file.write_text(json.dumps({"taker_slippage": res_taker, "maker": res_maker}, indent=2))
    print(f"Saved full replay report -> {out_file.relative_to(PROJECT_ROOT) if out_file.is_relative_to(PROJECT_ROOT) else out_file}")


if __name__ == "__main__":
    main()
