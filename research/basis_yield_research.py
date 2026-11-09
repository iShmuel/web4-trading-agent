"""Basis-yield (funding carry) research on REAL historical funding rates.

The replay harness stubs funding to a constant 0.012%/8h, so prior reports say
nothing about whether funding carry is actually harvestable. This script pulls
real 8h funding prints from Binance USDT-M perpetuals (public endpoint, no key)
and answers, per symbol:

* how often funding >= the engine's 8% APR hurdle, how often it is negative,
* how persistent a >= hurdle regime is (does a trailing signal predict the next days?),
* what a CURRENT-engine-style policy earns (instantaneous rate, zero hedge costs)
  versus a COSTED policy (4 hedge fills per round trip, persistence filter).

Output: reports/basis_yield_research.json (+ cached CSVs under data/, gitignored).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import ccxt
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUT = PROJECT_ROOT / "reports" / "basis_yield_research.json"
SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT", "LINK/USDT"]
PERIODS_PER_DAY = 3
HURDLE_APR = 8.0
FEE = 0.0006
SLIP = 0.0004
LEG_COST = FEE + SLIP  # per leg, per side (taker-style, consistent with the OOS cost model)


def fetch_history(sym: str, days: int = 365) -> pd.DataFrame:
    cache = DATA_DIR / f"funding_{sym.replace('/', '_')}_{days}d.csv"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 24 * 3600:
        return pd.read_csv(cache, parse_dates=["timestamp"]).set_index("timestamp")
    ex = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}, "timeout": 30000})
    since = ex.milliseconds() - days * 24 * 3600 * 1000
    rows: List[Dict[str, Any]] = []
    while True:
        batch = ex.fetch_funding_rate_history(f"{sym}:USDT", since=since, limit=1000)
        if not batch:
            break
        rows.extend({"timestamp": b["timestamp"], "rate": b["fundingRate"]} for b in batch)
        nxt = batch[-1]["timestamp"] + 1
        if nxt <= since or len(batch) < 2:
            break
        since = nxt
        if batch[-1]["timestamp"] >= ex.milliseconds() - 8 * 3600 * 1000:
            break
        time.sleep(ex.rateLimit / 1000.0)
    df = pd.DataFrame(rows).drop_duplicates("timestamp")
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    df.to_csv(cache)
    return df


def apr(rate_per_8h: float) -> float:
    return rate_per_8h * PERIODS_PER_DAY * 365 * 100.0


def simulate(rates: np.ndarray, entry_apr: float, exit_apr: float, window: int, cost: bool) -> Dict[str, Any]:
    """Hold a delta-neutral hedge while trailing mean APR >= thresholds (signal uses PAST prints only).

    current-engine style: window=1, entry=exit=hurdle, cost=False.
    The funding print at t is earned only if the hedge was already open BEFORE t (no lookahead).
    """
    in_pos = False
    pnl = 0.0  # as fraction of hedge notional, additive
    entries = 0
    earned = 0.0
    paid_costs = 0.0
    periods_in = 0
    neg_periods_in = 0
    equity = [0.0]
    for t in range(window, len(rates)):
        trailing = apr(float(np.mean(rates[t - window:t])))
        if in_pos:
            pnl += rates[t]
            earned += rates[t]
            periods_in += 1
            if rates[t] < 0:
                neg_periods_in += 1
            if trailing < exit_apr:
                in_pos = False
                if cost:
                    pnl -= 2 * LEG_COST
                    paid_costs += 2 * LEG_COST
        else:
            if trailing >= entry_apr:
                in_pos = True
                entries += 1
                if cost:
                    pnl -= 2 * LEG_COST
                    paid_costs += 2 * LEG_COST
        equity.append(pnl)
    if in_pos and cost:
        pnl -= 2 * LEG_COST
        paid_costs += 2 * LEG_COST
        equity[-1] = pnl
    days = (len(rates) - window) / PERIODS_PER_DAY
    eq = np.array(equity)
    dd = float(np.max(np.maximum.accumulate(eq) - eq)) if len(eq) else 0.0
    return {
        "net_return_pct_on_notional": round(pnl * 100, 3),
        "annualized_net_pct_on_notional": round(pnl * 100 * 365 / days, 2) if days > 0 else None,
        "gross_funding_earned_pct": round(earned * 100, 3),
        "hedge_costs_paid_pct": round(paid_costs * 100, 3),
        "entries": entries,
        "pct_time_in_position": round(100 * periods_in / max(1, len(rates) - window), 1),
        "negative_prints_while_in_position": neg_periods_in,
        "max_drawdown_pct_on_notional": round(dd * 100, 3),
    }


def persistence(rates: np.ndarray, lookback: int = 9, horizon: int = 21) -> Dict[str, Any]:
    """Does trailing-3-day mean >= hurdle predict the NEXT-7-day mean funding?"""
    hit, fwd_ok, fwd = 0, 0, []
    for t in range(lookback, len(rates) - horizon):
        if apr(float(np.mean(rates[t - lookback:t]))) >= HURDLE_APR:
            hit += 1
            f = apr(float(np.mean(rates[t:t + horizon])))
            fwd.append(f)
            fwd_ok += f >= HURDLE_APR
    return {
        "signal_observations": hit,
        "p_next_7d_mean_apr_ge_hurdle": round(fwd_ok / hit, 3) if hit else None,
        "mean_next_7d_apr_pct": round(float(np.mean(fwd)), 2) if fwd else None,
    }


def main() -> Dict[str, Any]:
    out: Dict[str, Any] = {"hurdle_apr_pct": HURDLE_APR, "leg_cost_per_side_pct": LEG_COST * 100, "symbols": {}}
    for sym in SYMBOLS:
        try:
            df = fetch_history(sym)
        except Exception as e:  # network / symbol availability
            out["symbols"][sym] = {"error": str(e)}
            continue
        r = df["rate"].astype(float).to_numpy()
        n = len(r)
        halves = {"first_half": r[: n // 2], "second_half": r[n // 2:]}
        res: Dict[str, Any] = {
            "prints": n,
            "span_days": round(n / PERIODS_PER_DAY, 1),
            "mean_apr_pct": round(apr(float(r.mean())), 2),
            "median_apr_pct": round(apr(float(np.median(r))), 2),
            "pct_prints_ge_hurdle": round(100 * float(np.mean([apr(x) >= HURDLE_APR for x in r])), 1),
            "pct_prints_negative": round(100 * float(np.mean(r < 0)), 1),
            "persistence_3d_signal_7d_forward": persistence(r),
            "current_engine_style_no_cost": simulate(r, HURDLE_APR, HURDLE_APR, 1, cost=False),
            "costed_instantaneous": simulate(r, HURDLE_APR, HURDLE_APR, 1, cost=True),
            "costed_persistent_3d_enter15_exit5": simulate(r, 15.0, 5.0, 9, cost=True),
            "costed_persistent_7d_enter12_exit4": simulate(r, 12.0, 4.0, 21, cost=True),
            "always_on_costed_once": {
                "annualized_net_pct_on_notional": round(
                    (float(r.sum()) - 2 * LEG_COST) * 100 * 365 / (n / PERIODS_PER_DAY), 2
                )
            },
        }
        for name, sub in halves.items():
            res[f"{name}_persistent_3d_costed"] = simulate(sub, 15.0, 5.0, 9, cost=True)
        out["symbols"][sym] = res
        print(f"{sym:<10} n={n} meanAPR={res['mean_apr_pct']}% >=hurdle={res['pct_prints_ge_hurdle']}% neg={res['pct_prints_negative']}%")
        print(f"   engine-style(no cost) ann={res['current_engine_style_no_cost']['annualized_net_pct_on_notional']}% | costed instant ann={res['costed_instantaneous']['annualized_net_pct_on_notional']}% (entries {res['costed_instantaneous']['entries']}) | costed persistent ann={res['costed_persistent_3d_enter15_exit5']['annualized_net_pct_on_notional']}%")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
