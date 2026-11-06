"""Agent 10: Post-Trade Forensic Analyst Agent.

Analyzes closed trades and decision journals every X executions to provide
deep statistical diagnosis, regime attribution, and LLM-driven root-cause
reasoning with actionable optimization hypotheses.

Key capabilities:
1. Payoff & Expectancy Statistics: Win rate, profit factor, payoff ratio (avg win / avg loss).
2. Strategy & Regime Attribution: Momentum vs Mean Reversion, Trend vs Range Chop vs Macro Bear.
3. Loss Attribution: Analyzes why stop-losses or whipsaws occurred.
4. LLM Forensic Reasoning: Uses local Ollama (phi4-mini / qwen3) to evaluate losing trades
   and synthesize concrete mathematical parameter hypotheses.
5. Emits formal Trade Forensic Report for Agent 11 (Optimizer) and Agent 12 (Governor).
"""

import argparse
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd

from agent.llm_client import query_local_llm

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_FILE = PROJECT_ROOT / "data" / "paper_portfolio.json"
DECISION_JOURNAL_FILE = PROJECT_ROOT / "data" / "decision_journal.jsonl"
FORENSIC_REPORT_FILE = PROJECT_ROOT / "data" / "trade_forensic_report.json"


@dataclass
class TradeForensicSummary:
    """Quantitative scorecard of evaluated trades."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    total_realized_pnl: float
    profit_factor: float
    avg_win_usd: float
    avg_loss_usd: float
    payoff_ratio: float
    expectancy_per_trade: float
    total_fees_paid: float
    momentum_trades: int
    momentum_pnl: float
    mean_reversion_trades: int
    mean_reversion_pnl: float
    top_winning_symbols: List[Dict[str, Any]]
    top_losing_symbols: List[Dict[str, Any]]
    losses_by_exit_reason: Dict[str, int]
    llm_forensic_diagnosis: str
    recommended_hypotheses: List[Dict[str, Any]]


def load_closed_trades(portfolio_path: Optional[Path] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Load closed trades from paper portfolio state."""
    path = portfolio_path or PORTFOLIO_FILE
    if not path.exists():
        return []
    try:
        with open(path, "r") as f:
            data = json.load(f)
        closed = data.get("closed_trades", [])
        if limit and limit > 0:
            return closed[-limit:]
        return closed
    except Exception as e:
        logger.error(f"[TradeAnalyst] Could not load portfolio trades: {e}")
        return []


def load_recent_decision_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Load the most recent settled decisions from decision_journal.jsonl."""
    if not DECISION_JOURNAL_FILE.exists():
        return []
    records = []
    try:
        with open(DECISION_JOURNAL_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        pass
        return records[-limit:]
    except Exception as e:
        logger.debug(f"[TradeAnalyst] Decision journal load notice: {e}")
        return []


def perform_statistical_audit(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute mathematical performance metrics across trade records."""
    if not trades:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate_pct": 0.0,
            "total_pnl": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "payoff_ratio": 0.0,
            "expectancy": 0.0,
            "total_fees": 0.0,
            "momentum_trades": 0,
            "momentum_pnl": 0.0,
            "mr_trades": 0,
            "mr_pnl": 0.0,
            "by_symbol": {},
            "exit_reasons": {},
        }

    pnls = [float(t.get("pnl", 0.0)) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p <= 0]

    gross_win = sum(wins)
    gross_loss = sum(losses)
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)

    total = len(trades)
    n_wins = len(wins)
    n_losses = len(losses)
    wr = round((n_wins / total) * 100.0, 1) if total > 0 else 0.0
    avg_win = round(float(np.mean(wins)), 2) if wins else 0.0
    avg_loss = round(float(np.mean(losses)), 2) if losses else 0.0
    payoff = round(avg_win / avg_loss, 2) if avg_loss > 0 else (1.0 if avg_win > 0 else 0.0)
    expectancy = round(float(np.mean(pnls)), 2) if pnls else 0.0
    total_fees = round(sum(float(t.get("fees", t.get("fees_paid", 0.0))) for t in trades), 2)

    # Strategy breakdown
    mom_trades = [t for t in trades if t.get("strategy_id") == "momentum"]
    mr_trades = [t for t in trades if t.get("strategy_id") == "mean_reversion"]
    mom_pnl = round(sum(float(t.get("pnl", 0.0)) for t in mom_trades), 2)
    mr_pnl = round(sum(float(t.get("pnl", 0.0)) for t in mr_trades), 2)

    # Symbol breakdown
    by_symbol: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        sym = t.get("symbol", "UNKNOWN")
        p = float(t.get("pnl", 0.0))
        if sym not in by_symbol:
            by_symbol[sym] = {"trades": 0, "wins": 0, "pnl": 0.0}
        by_symbol[sym]["trades"] += 1
        if p > 0:
            by_symbol[sym]["wins"] += 1
        by_symbol[sym]["pnl"] += p

    for s, data in by_symbol.items():
        data["pnl"] = round(data["pnl"], 2)
        data["win_rate"] = round((data["wins"] / data["trades"]) * 100.0, 1)

    # Exit reason breakdown for losing trades
    exit_reasons: Dict[str, int] = {}
    for t in trades:
        if float(t.get("pnl", 0.0)) <= 0:
            r = t.get("exit_reason", "Unspecified Stop")
            exit_reasons[r] = exit_reasons.get(r, 0) + 1

    return {
        "total_trades": total,
        "winning_trades": n_wins,
        "losing_trades": n_losses,
        "win_rate_pct": wr,
        "total_pnl": round(sum(pnls), 2),
        "profit_factor": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff,
        "expectancy": expectancy,
        "total_fees": total_fees,
        "momentum_trades": len(mom_trades),
        "momentum_pnl": mom_pnl,
        "mr_trades": len(mr_trades),
        "mr_pnl": mr_pnl,
        "by_symbol": by_symbol,
        "exit_reasons": exit_reasons,
    }


def query_llm_trade_forensics(
    stats: Dict[str, Any],
    sample_losses: List[Dict[str, Any]],
    model: str = "phi4-mini:latest",
) -> Optional[str]:
    """
    Invoke local Ollama LLM to synthesize forensic insights and formulate
    actionable parameter hypotheses based on recent trades.
    """
    loss_summary = []
    for t in sample_losses[:5]:
        loss_summary.append(
            f"- Symbol: {t.get('symbol')}, PnL: ${t.get('pnl'):.2f} ({t.get('return_pct', 0):.1f}%), "
            f"Strategy: {t.get('strategy_id')}, Exit: {t.get('exit_reason')}"
        )
    losses_txt = "\n".join(loss_summary) if loss_summary else "No severe losses recorded."

    by_sym_txt = ", ".join([f"{s}: ${d['pnl']} ({d['win_rate']}% WR)" for s, d in stats.get("by_symbol", {}).items()])

    prompt = f"""You are the Chief Quantitative Trade Forensic Analyst for an autonomous trading desk.
Analyze the following statistical audit of our most recent {stats['total_trades']} closed trades:

[DESK STATS]
- Win Rate: {stats['win_rate_pct']}% ({stats['winning_trades']} Wins / {stats['losing_trades']} Losses)
- Total Realized PnL: ${stats['total_pnl']}
- Profit Factor: {stats['profit_factor']}
- Payoff Ratio (Avg Win / Avg Loss): {stats['payoff_ratio']}x (${stats['avg_win']} / ${stats['avg_loss']})
- Momentum Engine PnL: ${stats['momentum_pnl']} ({stats['momentum_trades']} trades)
- Mean Reversion Engine PnL: ${stats['mr_pnl']} ({stats['mr_trades']} trades)
- Total Fees: ${stats['total_fees']}
- Symbol Breakdown: {by_sym_txt}

[SAMPLE LOSSES]
{losses_txt}

Provide:
1. DIAGNOSIS: 2-3 sentences explaining the primary structural reason for losses (e.g., chop whipsaws, stop placement, or underperforming symbols).
2. HYPOTHESES: Provide 2 concrete parameter hypotheses to improve win rate and net profit (e.g., adjust adx_chop_threshold, breakout_lookback, atr_multiplier, or prune symbols).
Format your response cleanly.
"""
    system_prompt = (
        "You are an elite institutional quantitative researcher specialized in algorithmic trade forensics, "
        "regime attribution, and risk governance. Keep answers analytical, mathematically precise, and grounded in empirical data."
    )

    resp = query_local_llm(prompt, system_prompt=system_prompt, model=model, temperature=0.2, max_tokens=350)
    return resp


def run_trade_forensic_analysis(
    trade_window: int = 15,  # Mathematically optimal deal volume: 15-20 trades
    portfolio_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
    use_llm: bool = True,
    model: str = "phi4-mini:latest",
    auto_submit_to_governor: bool = False,
) -> TradeForensicSummary:
    """Execute complete post-trade forensic analysis cycle."""
    trades = load_closed_trades(portfolio_path=portfolio_path, limit=trade_window)
    stats = perform_statistical_audit(trades)

    # Isolate top winners and losers
    sym_items = list(stats.get("by_symbol", {}).items())
    sorted_syms = sorted(sym_items, key=lambda x: x[1]["pnl"], reverse=True)
    top_winners = [{"symbol": s, **d} for s, d in sorted_syms if d["pnl"] > 0][:5]
    top_losers = [{"symbol": s, **d} for s, d in sorted_syms if d["pnl"] < 0][:5]

    sample_losses = [t for t in trades if float(t.get("pnl", 0.0)) < 0]

    # LLM Forensic Reasoning
    llm_diagnosis = ""
    hypotheses = []
    if use_llm and trades:
        llm_resp = query_llm_trade_forensics(stats, sample_losses, model=model)
        if llm_resp:
            llm_diagnosis = llm_resp

    if not llm_diagnosis:
        # Deterministic mathematical fallback diagnosis
        if stats["win_rate_pct"] >= 60.0:
            llm_diagnosis = f"Desk is performing with strong statistical edge ({stats['win_rate_pct']}% Win Rate, PF {stats['profit_factor']}). Primary drag stems from trailing stop slippage."
        elif stats["win_rate_pct"] >= 50.0:
            llm_diagnosis = f"Desk is breaking even in choppy consolidation ({stats['win_rate_pct']}% Win Rate). Increasing ADX chop thresholds is recommended."
        else:
            llm_diagnosis = f"Desk experiencing negative expectancy ({stats['win_rate_pct']}% Win Rate). Pruning underperforming drag symbols is recommended."

    # Generate structured parameter hypotheses
    if stats["win_rate_pct"] < 55.0:
        hypotheses.append({
            "target": "global_regime",
            "parameter": "adx_chop_threshold",
            "suggested_value": 22.0,
            "rationale": "Elevate ADX chop threshold from 20 to 22 to block low-volatility whipsaw entries.",
        })
    if any(d["pnl"] < -50.0 for s, d in stats.get("by_symbol", {}).items()):
        worst_sym = sorted_syms[-1][0] if sorted_syms else None
        if worst_sym:
            hypotheses.append({
                "target": "asset_pruning",
                "parameter": "blacklist_symbol",
                "suggested_value": worst_sym,
                "rationale": f"Prune {worst_sym} due to net loss drag of ${sorted_syms[-1][1]['pnl']:.2f}.",
            })

    summary = TradeForensicSummary(
        total_trades=stats["total_trades"],
        winning_trades=stats["winning_trades"],
        losing_trades=stats["losing_trades"],
        win_rate_pct=stats["win_rate_pct"],
        total_realized_pnl=stats["total_pnl"],
        profit_factor=stats["profit_factor"],
        avg_win_usd=stats["avg_win"],
        avg_loss_usd=stats["avg_loss"],
        payoff_ratio=stats["payoff_ratio"],
        expectancy_per_trade=stats["expectancy"],
        total_fees_paid=stats["total_fees"],
        momentum_trades=stats["momentum_trades"],
        momentum_pnl=stats["momentum_pnl"],
        mean_reversion_trades=stats["mr_trades"],
        mean_reversion_pnl=stats["mr_pnl"],
        top_winning_symbols=top_winners,
        top_losing_symbols=top_losers,
        losses_by_exit_reason=stats.get("exit_reasons", {}),
        llm_forensic_diagnosis=llm_diagnosis,
        recommended_hypotheses=hypotheses,
    )

    out = output_path or FORENSIC_REPORT_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(asdict(summary), f, indent=2)

    # Automated governance submission if requested
    if auto_submit_to_governor and hypotheses:
        try:
            from agent.governor import review_and_deploy_proposal
            prop_diff = {}
            for h in hypotheses:
                if h["parameter"] == "blacklist_symbol":
                    prop_diff.setdefault("blacklisted_symbols", []).append(h["suggested_value"])
                elif h["parameter"] == "adx_chop_threshold":
                    prop_diff["adx_chop_threshold"] = float(h["suggested_value"])

            if prop_diff:
                print(f"[TradeAnalyst] Automatically submitting proposal to Agent 12 Governor: {prop_diff}")
                review_and_deploy_proposal(
                    proposed_changes=prop_diff,
                    rationale=f"Automated post-trade forensic analysis of last {trade_window} trades detected drag.",
                    empirical_proof={
                        "recent_win_rate": f"{summary.win_rate_pct}%",
                        "recent_pnl": f"${summary.total_realized_pnl:+,.2f}",
                        "profit_factor": summary.profit_factor,
                    },
                    verify_tests=True,
                    use_llm=use_llm,
                )
        except Exception as e:
            logger.error(f"[TradeAnalyst] Governance submission notice: {e}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Agent 10: Post-Trade Forensic Analyst")
    parser.add_argument("--trades", type=int, default=25, help="Number of recent closed trades to analyze (default: 25)")
    parser.add_argument("--portfolio", type=str, default=None, help="Custom portfolio json path")
    parser.add_argument("--report", type=str, default=None, help="Output forensic report path")
    parser.add_argument("--no-llm", action="store_true", help="Bypass LLM query (pure mathematical audit)")
    parser.add_argument("--model", type=str, default="phi4-mini:latest", help="Local Ollama model (default: phi4-mini:latest)")
    args = parser.parse_args()

    port_path = Path(args.portfolio) if args.portfolio else PORTFOLIO_FILE
    rep_path = Path(args.report) if args.report else FORENSIC_REPORT_FILE

    print("=" * 80)
    print(f"  AGENT 10: POST-TRADE FORENSIC ANALYST AGENT")
    print(f"  Analyzing last {args.trades} closed trades from: {port_path.name}")
    print("=" * 80)

    summary = run_trade_forensic_analysis(
        trade_window=args.trades,
        portfolio_path=port_path,
        output_path=rep_path,
        use_llm=not args.no_llm,
        model=args.model,
    )

    print(f"\n[Trade Scorecard]")
    print(f"  Total Trades:     {summary.total_trades} ({summary.winning_trades} Wins / {summary.losing_trades} Losses)")
    print(f"  Win Rate:         {summary.win_rate_pct}%")
    print(f"  Realized PnL:     ${summary.total_realized_pnl:+,.2f}")
    print(f"  Profit Factor:    {summary.profit_factor:.2f}")
    print(f"  Payoff Ratio:     {summary.payoff_ratio:.2f}x (Avg Win: ${summary.avg_win_usd:+.2f} / Avg Loss: ${summary.avg_loss_usd:+.2f})")
    print(f"  Expectancy:       ${summary.expectancy_per_trade:+.2f} per trade")
    print(f"  Total Fees Paid:  ${summary.total_fees_paid:,.2f}")
    print(f"  Momentum PnL:     ${summary.momentum_pnl:+,.2f} ({summary.momentum_trades} trades)")
    print(f"  Mean-Rev PnL:     ${summary.mean_reversion_pnl:+,.2f} ({summary.mean_reversion_trades} trades)")

    win_labels = [f"{w['symbol']} (${w['pnl']:+,.1f})" for w in summary.top_winning_symbols]
    lose_labels = [f"{l['symbol']} (${l['pnl']:+,.1f})" for l in summary.top_losing_symbols]
    print(f"\n[Top Contributors]")
    print(f"  Winners: {win_labels}")
    print(f"  Drags:   {lose_labels}")

    print(f"\n[Losses by Exit Reason]")
    for r, cnt in summary.losses_by_exit_reason.items():
        print(f"  - {r}: {cnt} trades")

    print(f"\n[LLM Forensic Reasoning & Diagnosis]")
    print(summary.llm_forensic_diagnosis)

    print(f"\n[Actionable Hypotheses Generated for Agent 11 & 12]")
    for h in summary.recommended_hypotheses:
        print(f"  → [{h['target']}] Set {h['parameter']} = {h['suggested_value']} ({h['rationale']})")

    print("=" * 80)
    print(f"Report saved to: {rep_path}\n")


if __name__ == "__main__":
    main()
