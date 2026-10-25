"""Before/after backtest: global settings vs per-asset profiles.

Replays the REAL live stack (PortfolioCoordinator + RiskEngine + PaperAccount)
bar-by-bar over historical data, so results include the same fees (0.06%/side),
slippage (0.04%/side), position caps, strategy caps and circuit breakers as live.

Scenarios:
  A_current         : top-level config only (what the live bot did before)
  B_profiles        : per-asset profiles, incl. 15m bars for BTC/SOL + momentum 50% TP
  C_profiles_1h     : per-asset profiles but every asset on 1h bars (isolates timeframe)

Only reads market data and writes to a temporary directory. Never touches
data/paper_portfolio.json.

    python research/backtest_profiles.py --days 90
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.coordinator import PortfolioCoordinator, resolve_symbol_config  # noqa: E402
from core.data_loader import fetch_ohlcv  # noqa: E402
from core.paper_account import PaperAccount  # noqa: E402
from core.risk_engine import RiskEngine  # noqa: E402
from core.strategy import compute_indicators, compute_mean_reversion_indicators  # noqa: E402
import strategies.momentum as mom_mod  # noqa: E402
import strategies.mean_reversion as mr_mod  # noqa: E402

CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
TF_DELTA = {"15m": pd.Timedelta(minutes=15), "1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4)}
WINDOW = 60  # bars handed to strategies at each step (>= MIN_BARS_REQUIRED=50 and >= max_hold_bars=48)
WARMUP = 150  # bars required before an asset may trade (EMA100 + ADX need this)


def scenario_configs(base: dict) -> Dict[str, dict]:
    a = copy.deepcopy(base)
    a["use_asset_profiles"] = False
    b = copy.deepcopy(base)
    b["use_asset_profiles"] = True
    c = copy.deepcopy(base)
    c["use_asset_profiles"] = True
    for p in c.get("asset_profiles", {}).values():
        p["timeframe"] = "1h"
    out = {"A_current": a, "B_profiles": b, "C_profiles_1h": c}
    for cfg in out.values():
        cfg["max_stale_minutes"] = 0  # historical bars are "old" by definition
    return out


def load_data(pairs: set, days: int) -> Dict[tuple, pd.DataFrame]:
    data = {}
    for sym, tf in sorted(pairs):
        try:
            df = fetch_ohlcv(symbol=sym, timeframe=tf, days=days, force_refresh=False)
        except Exception as e:  # pragma: no cover - network dependent
            print(f"  ! could not load {sym} {tf}: {e}")
            continue
        if df is None or df.empty:
            continue
        df = df.sort_index()
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        # Re-index by bar CLOSE time so we only ever act on completed bars.
        df.index = df.index + TF_DELTA[tf]
        data[(sym, tf)] = df[~df.index.duplicated(keep="last")]
    return data


def run_scenario(name: str, cfg: dict, data: Dict[tuple, pd.DataFrame], watchlist: List[str], initial: float) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix=f"bt_{name}_"))
    account = PaperAccount(initial_capital=initial, portfolio_file=tmp / "portfolio.json")
    account._save = lambda: None  # in-memory simulation; no disk churn per bar
    risk = RiskEngine(
        default_risk_pct=cfg.get("account_risk_pct", 0.02),
        max_open_positions=cfg.get("max_concurrent_positions", 3),
    )
    coord = PortfolioCoordinator(account, risk, cfg)

    # Pre-compute causal indicators once per full series so each bar slice is O(1).
    series = {}
    for sym in watchlist:
        sc = resolve_symbol_config(cfg, sym)
        tf = sc.get("timeframe", cfg.get("timeframe", "1h"))
        if (sym, tf) not in data:
            continue
        raw = data[(sym, tf)]
        m_df = compute_indicators(
            raw,
            breakout_lookback=sc.get("breakout_lookback", 24),
            ema_trend_filter=sc.get("ema_trend_filter", 50),
            atr_period=sc.get("atr_period", 14),
        )
        r_df = compute_mean_reversion_indicators(
            raw,
            bb_period=sc.get("bb_period", 20),
            bb_std=sc.get("bb_std", 2.0),
            rsi_period=sc.get("rsi_period", 14),
            atr_period=sc.get("atr_period", 14),
            adx_period=sc.get("adx_period", 14),
        )
        for col in ("bb_sma", "bb_upper", "bb_lower", "bb_width", "z_score", "rsi", "adx"):
            m_df[col] = r_df[col]
        series[sym] = m_df
    if not series:
        raise RuntimeError("no data loaded")

    try:
        mom_mod.compute_indicators = lambda df, **_: df
        mr_mod.compute_mean_reversion_indicators = lambda df, **_: df

        # Each asset starts trading once it has WARMUP completed bars (stocks have far fewer bars than crypto).
        start_idx = {sym: min(WARMUP, len(df) - 1) for sym, df in series.items()}
        timeline = sorted(set().union(*[set(df.index[start_idx[sym]:]) for sym, df in series.items()]))
        pos_idx = dict(start_idx)

        last_price: Dict[str, float] = {}
        equity_curve = []
        cur_day = None
        for t in timeline:
            if t.date() != cur_day:  # new simulated day -> new daily-loss baseline
                cur_day = t.date()
                risk.daily_start_equity = 0.0
            market = {}
            for sym, df in series.items():
                i = pos_idx[sym]
                if i < len(df) and df.index[i] == t:
                    market[sym] = df.iloc[max(0, i - WINDOW + 1): i + 1]
                    last_price[sym] = float(df.iloc[i]["close"])
                    pos_idx[sym] = i + 1
            if not market:
                continue
            prev_open = set(account.positions.keys())
            coord.run_cycle(market)
            for sym in set(account.positions.keys()) - prev_open:
                account.positions[sym]["entry_time"] = t.isoformat()
            equity_curve.append((t, account.get_total_equity(dict(last_price))))
    finally:
        mom_mod.compute_indicators = compute_indicators
        mr_mod.compute_mean_reversion_indicators = compute_mean_reversion_indicators

    # Close anything still open at the last price (marks the result to market, with exit fee).
    for sym in list(account.positions):
        if sym in last_price:
            account.close_position(sym, last_price[sym] * 0.9996, exit_reason="End of backtest")

    return summarize(name, account, equity_curve, initial)


def summarize(name: str, account: PaperAccount, curve: list, initial: float) -> dict:
    trades = account.state["closed_trades"]
    positions = defaultdict(lambda: {"pnl": 0.0, "strategy": "", "symbol": "", "partial": False})
    for t in trades:
        key = (t["symbol"], t["entry_time"])
        positions[key]["pnl"] += t["pnl"]
        positions[key]["strategy"] = t["strategy_id"]
        positions[key]["symbol"] = t["symbol"]
        if "Partial" in t.get("exit_reason", "") or "Scale-Out" in t.get("exit_reason", ""):
            positions[key]["partial"] = True

    pos_list = list(positions.values())
    wins = [p for p in pos_list if p["pnl"] > 0]
    gross_win = sum(p["pnl"] for p in wins)
    gross_loss = -sum(p["pnl"] for p in pos_list if p["pnl"] <= 0)

    eq = pd.Series([e for _, e in curve]) if curve else pd.Series([initial])
    max_dd = float(((eq.cummax() - eq) / eq.cummax()).max() * 100)
    final = account.cash

    by_strat = defaultdict(lambda: [0, 0.0])
    by_sym = defaultdict(lambda: [0, 0.0])
    for p in pos_list:
        by_strat[p["strategy"]][0] += 1
        by_strat[p["strategy"]][1] += p["pnl"]
        by_sym[p["symbol"]][0] += 1
        by_sym[p["symbol"]][1] += p["pnl"]

    return {
        "scenario": name,
        "final_equity": round(final, 2),
        "return_pct": round((final - initial) / initial * 100, 2),
        "trades": len(pos_list),
        "win_rate_pct": round(len(wins) / len(pos_list) * 100, 1) if pos_list else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_drawdown_pct": round(max_dd, 2),
        "fees_paid": round(sum(t.get("fees", 0.0) for t in trades), 2),
        "partial_take_profits": sum(1 for p in pos_list if p["partial"]),
        "by_strategy": {k: {"trades": v[0], "pnl": round(v[1], 2)} for k, v in by_strat.items()},
        "by_symbol": {k: {"trades": v[0], "pnl": round(v[1], 2)} for k, v in by_sym.items()},
        "period": f"{curve[0][0]:%Y-%m-%d} -> {curve[-1][0]:%Y-%m-%d}" if curve else "n/a",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--crypto-only", action="store_true")
    args = ap.parse_args()

    os.fsync = lambda fd: None  # speed: temp files only, durability irrelevant here

    base = json.loads(CONFIG_PATH.read_text())
    watchlist = base.get("watchlist", [])
    if args.crypto_only:
        watchlist = [s for s in watchlist if "/" in s]
    scenarios = scenario_configs(base)

    pairs = set()
    for cfg in scenarios.values():
        for s in watchlist:
            pairs.add((s, resolve_symbol_config(cfg, s).get("timeframe", cfg.get("timeframe", "1h"))))
    print(f"Loading {args.days}d of data for {sorted(pairs)} ...")
    data = load_data(pairs, args.days)

    results = []
    for name, cfg in scenarios.items():
        print(f"Running {name} ...", flush=True)
        results.append(run_scenario(name, cfg, data, watchlist, args.capital))

    print("\n" + "=" * 96)
    print(f"{'SCENARIO':<16}{'RETURN':>9}{'FINAL $':>12}{'TRADES':>8}{'WIN%':>7}{'PF':>6}{'MAX DD':>9}{'FEES $':>9}{'50% TPs':>9}")
    print("-" * 96)
    for r in results:
        pf = f"{r['profit_factor']:.2f}" if r["profit_factor"] is not None else "n/a"
        print(f"{r['scenario']:<16}{r['return_pct']:>8.2f}%{r['final_equity']:>12,.2f}{r['trades']:>8}{r['win_rate_pct']:>6.1f}%"
              f"{pf:>6}{r['max_drawdown_pct']:>8.2f}%{r['fees_paid']:>9.2f}{r['partial_take_profits']:>9}")
    print("=" * 96)
    for r in results:
        print(f"\n{r['scenario']}  ({r['period']})")
        print("  by strategy:", ", ".join(f"{k}: {v['trades']} trades ${v['pnl']:+.2f}" for k, v in r["by_strategy"].items()) or "none")
        print("  by symbol  :", ", ".join(f"{k}: {v['trades']} ${v['pnl']:+.2f}" for k, v in r["by_symbol"].items()) or "none")

    out = PROJECT_ROOT / "data" / "profile_backtest_results.json"
    out.write_text(json.dumps({"days": args.days, "watchlist": watchlist, "results": results}, indent=2))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
