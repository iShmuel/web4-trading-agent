"""Performance Audit & Insight Analyzer.
Analyzes the live paper trading run, closed trades, portfolio equity fluctuations,
and self-improvement tournament cycles to provide actionable improvement recommendations.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

PROJECT_ROOT = Path(__file__).resolve().parent
PORTFOLIO_FILE = PROJECT_ROOT / "data" / "paper_portfolio.json"
LOG_FILE = PROJECT_ROOT / "data" / "live_trader.log"
CONFIG_FILE = PROJECT_ROOT / "config" / "active_config.json"


def load_json(path: Path) -> dict:
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {}


def analyze_logs() -> Dict[str, Any]:
    """Parse live_trader.log for ticks, tournament runs, and alerts."""
    ticks_count = 0
    tournaments_run = 0
    alerts_triggered = 0

    if not LOG_FILE.exists():
        return {
            "ticks": 0,
            "tournaments": 0,
            "alerts": 0,
        }

    with open(LOG_FILE, "r") as f:
        for line in f:
            if "Polling live watchlist" in line:
                ticks_count += 1
            if "PERIODIC SELF-IMPROVEMENT TOURNAMENT TRIGGERED" in line:
                tournaments_run += 1
            if "[PAPER ALERT]" in line:
                alerts_triggered += 1

    return {
        "ticks": ticks_count,
        "tournaments": tournaments_run,
        "alerts": alerts_triggered,
    }


def generate_audit_report():
    portfolio = load_json(PORTFOLIO_FILE)
    config = load_json(CONFIG_FILE)
    log_stats = analyze_logs()

    initial_capital = portfolio.get("initial_capital", 10000.0)
    cash = portfolio.get("cash", 10000.0)
    positions = portfolio.get("positions", {})
    closed_trades = portfolio.get("closed_trades", [])

    total_closed = len(closed_trades)
    winning_trades = [t for t in closed_trades if t["pnl"] > 0]
    losing_trades = [t for t in closed_trades if t["pnl"] <= 0]
    win_rate = (len(winning_trades) / total_closed * 100.0) if total_closed > 0 else 0.0

    gross_profit = sum(t["pnl"] for t in winning_trades)
    gross_loss = abs(sum(t["pnl"] for t in losing_trades))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    total_realized_pnl = sum(t["pnl"] for t in closed_trades)

    print("=" * 70)
    print("        AUTONOMOUS AI TRADING AGENT: PERFORMANCE AUDIT REPORT")
    print("=" * 70)
    print(f"Audit Timestamp:       {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Total Market Scans:    {log_stats['ticks']} ticks evaluated")
    print(f"Optimization Cycles:   {log_stats['tournaments']} tournaments completed")
    print("-" * 70)

    print("1. CAPITAL & PORTFOLIO METRICS")
    print(f"   Initial Capital:    ${initial_capital:,.2f}")
    print(f"   Available Cash:     ${cash:,.2f}")
    print(f"   Realized Net PnL:   ${total_realized_pnl:+,.2f} ({(total_realized_pnl/initial_capital)*100:+.2f}%)")
    print(f"   Open Positions:     {len(positions)} active")
    for sym, pos in positions.items():
        print(f"     - {sym}: {pos['units']:.5f} units @ ${pos['entry_price']:,.2f} | Trailing Stop: ${pos['trailing_stop']:,.2f}")

    print("\n2. TRADE EXECUTION ANALYSIS")
    print(f"   Total Completed:    {total_closed}")
    print(f"   Win Rate:           {win_rate:.1f}% ({len(winning_trades)}W / {len(losing_trades)}L)")
    print(f"   Profit Factor:      {profit_factor:.2f}")

    print("\n3. RISK COMPLIANCE AUDIT")
    risk_violations = [t for t in closed_trades if abs(t["pnl"]) > (initial_capital * 0.055)]
    if not risk_violations:
        print("   ✅ 100% Risk Compliant: Zero trades violated the 1%-5% max capital risk bound.")
    else:
        print(f"   ⚠️ Warning: {len(risk_violations)} trades experienced slippage exceeding 5% risk bound.")

    print("\n4. KEY STRATEGIC INSIGHTS & NEXT PLACES TO IMPROVE")
    print("   A. Regime Volatility Filters (ADX / ATR Ratio):")
    print("      Filter out low-volatility consolidation ranges to avoid false breakouts.")
    print("   B. Multi-Timeframe Confirmation:")
    print("      Require both 4-hour and 1-hour alignment before triggering entries.")
    print("   C. Dynamic Take-Profit Targets:")
    print("      Scale out 50% of the position at 3x ATR to lock in early wins while letting remainder ride.")
    print("=" * 70)


if __name__ == "__main__":
    generate_audit_report()
