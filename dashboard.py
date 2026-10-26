"""Real-time, read-only terminal dashboard for the live paper trading bot.

Shows what the bot is doing right now: whether it is running, account equity,
open positions (live P&L and distance to stop), every watched asset's latest
price and decision, and the most recent trade alerts.

It ONLY READS files. It never writes the portfolio, so it is safe to run while
the bot is running (and as many dashboards as you like).

    python dashboard.py              # refresh every 5s (Ctrl+C to quit)
    python dashboard.py --interval 2
    python dashboard.py --once       # print one snapshot and exit

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.process_lock import is_locked, read_lock_pid  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data"
PORTFOLIO_FILE = DATA_DIR / "paper_portfolio.json"
STATUS_FILE = DATA_DIR / "live_status.json"
LOG_FILE = DATA_DIR / "live_trader.log"
LOCK_FILE = DATA_DIR / "live_trader.lock"

STALE_AFTER_SECONDS = 180
WIDTH = 120

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def green(t: str) -> str:
    return _c(t, "32")


def red(t: str) -> str:
    return _c(t, "31")


def yellow(t: str) -> str:
    return _c(t, "33")


def bold(t: str) -> str:
    return _c(t, "1")


def dim(t: str) -> str:
    return _c(t, "2")


def signed(value: float, text: str) -> str:
    if value > 0:
        return green(text)
    if value < 0:
        return red(text)
    return text


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def tail_lines(path: Path, max_bytes: int = 400_000) -> List[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return [ln.rstrip("\r") for ln in data.splitlines()]


def recent_alerts(lines: List[str], limit: int = 8) -> List[str]:
    """Extract '[PAPER ALERT]' events with the timestamp of their polling cycle."""
    events: List[str] = []
    last_ts = ""
    for i, ln in enumerate(lines):
        if "Polling live watchlist" in ln and ln.startswith("["):
            last_ts = ln[1:ln.find("]")]
        elif "[PAPER ALERT]" in ln:
            headline = ln.strip()
            detail = lines[i + 1].strip() if i + 1 < len(lines) else ""
            events.append(f"{dim(last_ts)}  {headline}\n{'':>25}{detail}")
    return events[-limit:]


def age_seconds(iso_ts: Optional[str]) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(iso_ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except ValueError:
        return None


def render() -> str:
    out: List[str] = []
    rule = "─" * WIDTH
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    portfolio = read_json(PORTFOLIO_FILE) or {}
    status = read_json(STATUS_FILE) or {}
    prices: Dict[str, float] = status.get("prices", {}) or {}

    out.append(bold(f" WEB4 PAPER TRADING — LIVE DASHBOARD".ljust(WIDTH - len(now)) + now))
    out.append(rule)

    # ── Bot status ────────────────────────────────────────────────
    running = is_locked(LOCK_FILE)
    age = age_seconds(status.get("updated_at"))
    if running:
        state = green(f"● RUNNING (PID {read_lock_pid(LOCK_FILE) or '?'})")
    else:
        state = red("○ STOPPED — start with: python live_paper_trader.py --loop")
    if age is None:
        freshness = yellow("no scan recorded yet")
    elif age > STALE_AFTER_SECONDS:
        freshness = yellow(f"last scan {int(age)}s ago (stale)")
    else:
        freshness = f"last scan {int(age)}s ago"
    out.append(f" Bot: {state}   |   {freshness}   |   {dim('PAPER ONLY — no real money')}")
    out.append(rule)

    # ── Account ───────────────────────────────────────────────────
    initial = float(portfolio.get("initial_capital", 10000.0))
    cash = float(portfolio.get("cash", initial))
    positions: Dict[str, Dict[str, Any]] = portfolio.get("positions", {}) or {}
    trades: List[Dict[str, Any]] = portfolio.get("closed_trades", []) or []

    pos_value = 0.0
    unrealized = 0.0
    for sym, p in positions.items():
        cur = float(prices.get(sym, p["entry_price"]))
        if p.get("side", "LONG") == "SHORT":
            pnl = (p["entry_price"] - cur) * p["units"]
            pos_value += p["units"] * p["entry_price"] + pnl
        else:
            pnl = (cur - p["entry_price"]) * p["units"]
            pos_value += p["units"] * cur
        unrealized += pnl
    equity = cash + pos_value
    total_ret = (equity - initial) / initial * 100.0
    realized = sum(t.get("pnl", 0.0) for t in trades)
    wins = sum(1 for t in trades if t.get("pnl", 0.0) > 0)

    out.append(bold(" ACCOUNT"))
    out.append(
        f"  Equity: {bold(f'${equity:,.2f}')}  ({signed(total_ret, f'{total_ret:+.2f}%')} vs ${initial:,.0f})"
        f"   Cash: ${cash:,.2f}   Unrealized: {signed(unrealized, f'${unrealized:+,.2f}')}"
    )
    wr = f"{wins / len(trades) * 100:.0f}%" if trades else "n/a"
    out.append(
        f"  Realized P&L: {signed(realized, f'${realized:+,.2f}')}   Closed fills: {len(trades)} {dim('(50% take-profits count as a fill)')}"
        f"   Win rate: {wr} ({wins}W / {len(trades) - wins}L)"
    )
    out.append(rule)

    # ── Open positions ────────────────────────────────────────────
    out.append(bold(f" OPEN POSITIONS ({len(positions)})"))
    if not positions:
        out.append(dim("  None — waiting for a setup."))
    else:
        out.append(dim(f"  {'SYMBOL':<10}{'STRATEGY':<15}{'SIDE':<6}{'UNITS':>10}{'ENTRY':>12}{'NOW':>12}{'STOP':>12}{'P&L':>14}  TO STOP"))
        for sym, p in positions.items():
            side = p.get("side", "LONG")
            entry = float(p["entry_price"])
            cur = float(prices.get(sym, entry))
            stop = float(p.get("trailing_stop", 0.0))
            if side == "SHORT":
                pnl = (entry - cur) * p["units"]
                to_stop = (stop - cur) / cur * 100.0 if cur else 0.0
            else:
                pnl = (cur - entry) * p["units"]
                to_stop = (cur - stop) / cur * 100.0 if cur else 0.0
            pnl_pct = pnl / (entry * p["units"]) * 100.0 if entry else 0.0
            pnl_txt = signed(pnl, f"{f'${pnl:+,.2f}':>10} {pnl_pct:+.1f}%")
            stop_txt = red(f"{to_stop:.2f}%") if to_stop < 0.5 else f"{to_stop:.2f}%"
            # A partial take-profit is recorded as a closed trade sharing this position's entry_time.
            banked = [t for t in trades if t.get("symbol") == sym and t.get("entry_time") == p.get("entry_time")]
            banked_txt = ""
            if banked:
                banked_pnl = sum(t.get("pnl", 0.0) for t in banked)
                banked_txt = "  " + green("✓ 50% banked ") + signed(banked_pnl, f"${banked_pnl:+,.2f}")
            out.append(
                f"  {sym:<10}{p.get('strategy_id', '?'):<15}{side:<6}{p['units']:>10.4f}"
                f"{entry:>12,.2f}{cur:>12,.2f}{stop:>12,.2f}  {pnl_txt}  {stop_txt}{banked_txt}"
            )
    out.append(rule)

    # ── Watchlist ─────────────────────────────────────────────────
    out.append(bold(" LIVE WATCHLIST — WHAT NEEDS TO HAPPEN TO ENTER A TRADE"))
    header = (
        f"  {'SYMBOL':<10} │ {'PRICE':>10} │ {'BOT STATUS':<11} │ {'NEXT TRIGGER':<15} │ "
        f"{'MOMENTUM SETUP (Breakout & Trend)':<34} │ {'MEAN REVERSION SETUP':<24}"
    )
    out.append(dim(header))
    out.append(dim("  " + "─" * (WIDTH - 4)))

    assets: Dict[str, Dict[str, Any]] = status.get("assets", {}) or {}
    if not assets:
        out.append(dim("  No scan snapshot yet (it appears after the bot's next scan)."))
    for sym, a in assets.items():
        price = a.get("price")
        price_txt = f"${price:>9,.2f}" if isinstance(price, (int, float)) and price else f"{'—':>10}"
        action = a.get("action", "?")
        reason = str(a.get("reason", ""))
        ind = a.get("indicators", {}) or {}

        if action == "REJECTED" and reason.lower().startswith("no entry signal"):
            action = "WAITING"
        tag = {
            "ENTER_LONG": green("● BUY NOW"), "ENTER_SHORT": red("● SHORT NOW"), "EXIT": red("● EXIT NOW"),
            "PARTIAL_EXIT": green("● TAKE 50%"), "HOLD": bold("● HOLDING"), "CONFLICT": yellow("⚡ CONFLICT"),
            "REJECTED": yellow("⚠ BLOCKED"), "NO_DATA": dim("○ MARKET OFF"), "WAITING": dim("○ SCANNING"),
        }.get(action, dim(action))

        # Format trigger gap and technical indicators
        bh = ind.get("breakout_high")
        ema = ind.get("ema_filter")
        rsi = ind.get("rsi")
        adx = ind.get("adx")
        bb_low = ind.get("bb_lower")

        # 1. Clear explanation of Next Trigger
        if action == "HOLD":
            stop = a.get("stop_loss", 0.0)
            if price and stop and price > 0:
                dist = abs((price - stop) / price) * 100
                next_trigger = bold(f"Stop: ${stop:,.2f} ({dist:.1f}%)")
            else:
                next_trigger = bold("Position Active")
        elif isinstance(price, (int, float)) and price > 0 and isinstance(bh, (int, float)) and bh > 0:
            gap = ((bh - price) / price) * 100
            if gap <= 0.5:
                next_trigger = green(f"Breakout +{gap:.2f}%")
            elif gap <= 1.2:
                next_trigger = yellow(f"Breakout +{gap:.2f}%")
            else:
                next_trigger = f"Breakout +{gap:.2f}%"
        elif "NO DATA" in tag or price_txt == "         —":
            next_trigger = dim("Opens Mon 15:30")
        else:
            next_trigger = dim("Awaiting data")

        # 2. Clear Momentum Telemetry
        if action == "HOLD":
            mom_setup = green("● Holding active position")
        elif isinstance(bh, (int, float)) and isinstance(ema, (int, float)):
            trend_label = green("Uptrend ▲") if price and price > ema else red("Downtrend ▼")
            mom_setup = f"Needs >${bh:,.1f} ({trend_label})"
        elif "NO DATA" in tag or price_txt == "         —":
            mom_setup = dim("US Market opens 15:30")
        else:
            mom_setup = dim("Scanning...")

        # 3. Clear Mean Reversion Telemetry
        if isinstance(rsi, (int, float)) and isinstance(adx, (int, float)):
            adx_state = green("Calm") if adx < 25 else "Trend"
            rsi_state = green("Oversold") if rsi < 30 else ("Neutral" if rsi < 65 else red("Overbought"))
            mr_setup = f"RSI:{rsi:.0f}({rsi_state}) ADX:{adx:.0f}({adx_state})"
        else:
            mr_setup = dim("—")

        pipe = dim("│")
        out.append(
            f"  {sym:<10} {pipe} {price_txt} {pipe} {tag:<19} {pipe} {next_trigger:<23} {pipe} "
            f"{mom_setup:<42} {pipe} {mr_setup}"
        )
    out.append(rule)

    # ── Recent events ─────────────────────────────────────────────
    out.append(bold(" RECENT EVENTS"))
    events = recent_alerts(tail_lines(LOG_FILE))
    if not events:
        out.append(dim("  No trades yet in the recent log."))
    for e in events:
        out.append(f"  {e}")
    out.append(rule)
    out.append(dim(" Read-only view. Ctrl+C to quit. Full log: tail -f data/live_trader.log"))
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Real-time read-only dashboard for the paper trading bot")
    parser.add_argument("--interval", type=float, default=5.0, help="Refresh interval in seconds (default: 5)")
    parser.add_argument("--once", action="store_true", help="Print a single snapshot and exit")
    args = parser.parse_args()

    if args.once:
        print(render())
        return

    try:
        while True:
            frame = render()
            if USE_COLOR:
                sys.stdout.write("\033[H\033[2J")
            sys.stdout.write(frame + "\n")
            sys.stdout.flush()
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
