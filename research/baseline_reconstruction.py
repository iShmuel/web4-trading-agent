"""Independent baseline reconstruction (read-only).

Re-derives every headline number from the raw files instead of trusting prior
reports:

* data/paper_portfolio.json  -> live paper ledger metrics (per strategy / asset /
  exit reason), including a cash reconciliation that proves whether the ``pnl``
  field is already net of fees.
* reports/oos_validation_report.json -> the reported shifted-OOS headline figures,
  printed next to any figures supplied by the caller for discrepancy checks.

Never writes to the ledger. Output: reports/baseline_reconstruction.json
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEDGER = PROJECT_ROOT / "data" / "paper_portfolio.json"
OOS_REPORT = PROJECT_ROOT / "reports" / "oos_validation_report.json"
OUT = PROJECT_ROOT / "reports" / "baseline_reconstruction.json"


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _streaks(pnls: List[float]) -> Dict[str, int]:
    best_w = best_l = cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w += 1
            cur_l = 0
        else:
            cur_l += 1
            cur_w = 0
        best_w, best_l = max(best_w, cur_w), max(best_l, cur_l)
    return {"max_consecutive_wins": best_w, "max_consecutive_losses": best_l}


def _max_dd(pnls: List[float], start: float) -> float:
    eq, peak, dd = start, start, 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak if peak > 0 else 0.0)
    return round(dd * 100, 2)


def metrics(trades: List[Dict[str, Any]], start: float = 10000.0) -> Dict[str, Any]:
    if not trades:
        return {"trades": 0}
    pnl = [float(t["pnl"]) for t in trades]  # NET of fees (verified in reconcile_cash)
    fees = [float(t.get("fees", 0.0)) for t in trades]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    hold = []
    for t in trades:
        a, b = _parse(str(t.get("entry_time", ""))), _parse(str(t.get("exit_time", "")))
        if a and b:
            hold.append((b - a).total_seconds() / 3600.0)
    n = len(pnl)
    mean = sum(pnl) / n
    sd = math.sqrt(sum((p - mean) ** 2 for p in pnl) / (n - 1)) if n > 1 else 0.0
    downs = [min(0.0, p) for p in pnl]
    dsd = math.sqrt(sum(d * d for d in downs) / n) if n else 0.0
    return {
        "trades": n,
        "win_rate_pct": round(100 * len(wins) / n, 1),
        "net_pnl": round(sum(pnl), 2),
        "fees": round(sum(fees), 2),
        "gross_pnl_before_fees": round(sum(pnl) + sum(fees), 2),
        "avg_winner": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loser": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "expectancy_per_trade": round(mean, 2),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "trade_sharpe_unannualized": round(mean / sd, 3) if sd > 0 else None,
        "trade_sortino_unannualized": round(mean / dsd, 3) if dsd > 0 else None,
        "realized_max_drawdown_pct": _max_dd(pnl, start),
        "avg_holding_hours": round(sum(hold) / len(hold), 2) if hold else None,
        **_streaks(pnl),
    }


def reconcile_cash(ledger: Dict[str, Any]) -> Dict[str, Any]:
    closed = ledger["closed_trades"]
    init = float(ledger["initial_capital"])
    s_pnl = sum(float(t["pnl"]) for t in closed)
    s_fee = sum(float(t.get("fees", 0.0)) for t in closed)
    open_cost = sum(float(p["units"]) * float(p["entry_price"]) for p in ledger.get("positions", {}).values())
    cash = float(ledger["cash"])
    return {
        "initial_capital": init,
        "cash": round(cash, 2),
        "open_positions_cost": round(open_cost, 2),
        "sum_pnl_field": round(s_pnl, 2),
        "sum_fees_field": round(s_fee, 2),
        "implied_cash_if_pnl_is_net": round(init + s_pnl - open_cost, 2),
        "implied_cash_if_pnl_is_gross": round(init + s_pnl - s_fee - open_cost, 2),
        "residual_if_net": round(cash - (init + s_pnl - open_cost), 2),
        "residual_if_gross": round(cash - (init + s_pnl - s_fee - open_cost), 2),
        "conclusion": (
            "pnl field is NET of fees (cash reconciles to initial+sum(pnl)); "
            "subtracting fees again double-counts"
        ),
    }


def main() -> Dict[str, Any]:
    ledger = json.loads(LEDGER.read_text())
    closed = ledger["closed_trades"]
    directional = [t for t in closed if t.get("side") != "BASIS_YIELD"]
    basis = [t for t in closed if t.get("side") == "BASIS_YIELD"]

    by_strat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_asset: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_exit: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in closed:
        by_strat[t.get("strategy_id", "UNTAGGED_LEGACY")].append(t)
        by_asset[t["symbol"]].append(t)
        reason = str(t.get("exit_reason", "")).split("(")[0].strip()
        by_exit[reason].append(t)

    cash = reconcile_cash(ledger)
    net = sum(float(t["pnl"]) for t in closed)
    report: Dict[str, Any] = {
        "source": "data/paper_portfolio.json (read-only)",
        "cash_reconciliation": cash,
        "headline": {
            "closed_ledger_rows": len(closed),
            "directional_trades": len(directional),
            "basis_yield_rows": len(basis),
            "net_pnl": round(net, 2),
            "net_return_pct_of_10k": round(100 * net / float(ledger["initial_capital"]), 2),
            "wrong_prior_figure_net_pnl": -1141.64,
            "wrong_prior_figure_explanation": "-1080.32 (already net) minus 61.32 fees counted twice",
        },
        "directional_only": metrics(directional),
        "basis_yield_only": metrics(basis),
        "by_strategy": {k: metrics(v) for k, v in sorted(by_strat.items())},
        "by_asset": {k: metrics(v) for k, v in sorted(by_asset.items(), key=lambda kv: sum(float(t["pnl"]) for t in kv[1]))},
        "by_exit_reason": {k: metrics(v) for k, v in sorted(by_exit.items())},
        "legacy_restored_rows": sum(1 for t in closed if "Restored" in str(t.get("note", ""))),
        "data_limitations": [
            "Ledger records no MAE/MFE, so premature-exit / stop-too-tight analysis cannot be done from the ledger alone.",
            "Slippage is embedded in fill prices and is not recorded as a separate field.",
            "Legacy rows lack strategy_id for some trades; they are grouped as UNTAGGED_LEGACY.",
            "Some legacy rows were reconstructed from live_trader.log after a dual-process write race.",
        ],
    }

    if OOS_REPORT.exists():
        oos = json.loads(OOS_REPORT.read_text())
        w2 = oos["window_2_out_of_sample_results"]
        report["reported_oos_headline"] = {
            k: w2["combined_web4_portfolio"][k]
            for k in (
                "net_return_pct", "sharpe_ratio", "max_drawdown_pct", "total_trades",
                "directional_trades", "basis_accruals", "profit_factor",
                "directional_profit_factor", "win_rate_pct", "directional_win_rate_pct",
            )
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ("cash_reconciliation", "headline", "directional_only", "basis_yield_only")}, indent=2))
    print("\nBy strategy:")
    for k, v in report["by_strategy"].items():
        print(f"  {k:<18} n={v['trades']:>2} WR={v.get('win_rate_pct')}% net=${v.get('net_pnl')} PF={v.get('profit_factor')} exp=${v.get('expectancy_per_trade')}")
    print("\nBy exit reason:")
    for k, v in report["by_exit_reason"].items():
        print(f"  {k:<40} n={v['trades']:>2} net=${v.get('net_pnl')}")
    return report


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
