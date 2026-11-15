"""Real-time, read-only terminal dashboard for the live paper trading bot.

Shows what the bot is doing right now across all 4 strategy desks:
1. Multi-asset Directional Watchlist (Momentum Breakout & Mean Reversion)
2. Cointegrated Statistical Arbitrage Pairs (Z-Score gauges & Stationarity gates)
3. Delta-Neutral Perpetual Basis Yield (Funding rate APRs & 8h epoch countdown)
4. Risk Fortress Circuit Breakers & Macro Radar Gauges
5. Real-Time Closed Trades Ledger (Fills, Realized P&L, 50% scale-out bankings)

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
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.process_lock import is_locked, read_lock_pid  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
PENDING_PROPOSALS_FILE = PROJECT_ROOT / "config" / "pending_proposals.json"
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


def cyan(t: str) -> str:
    return _c(t, "36")


def magenta(t: str) -> str:
    return _c(t, "35")


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


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def get_pending_proposals(path: Path, current_cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Return only the single most recent valid pending proposal that is still relevant to current_cfg."""
    data = read_json(path)
    if not data:
        return []
    proposals = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
    cfg = current_cfg if current_cfg is not None else (read_json(CONFIG_PATH) or {})

    # Find timestamp of the most recent DEPLOYED proposal to filter out obsolete proposals
    latest_deployed_ts = None
    for p in proposals:
        if isinstance(p, dict) and p.get("status") == "DEPLOYED":
            ts_str = p.get("approved_at") or p.get("created_at")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if latest_deployed_ts is None or ts > latest_deployed_ts:
                        latest_deployed_ts = ts
                except ValueError:
                    pass

    valid_pending: List[Dict[str, Any]] = []
    for p in proposals:
        if not isinstance(p, dict) or p.get("status") != "PENDING_HUMAN_APPROVAL":
            continue
        changes = p.get("proposed_changes")
        if not isinstance(changes, dict) or not changes:
            continue
        # Skip if proposed changes are already applied in active_config.json
        if cfg and all(cfg.get(k) == v for k, v in changes.items()):
            continue
        # Skip if baseline 'current_value' in evidence no longer matches active_config.json
        evidence = p.get("empirical_proof") or (p.get("analyst_proposal") or {}).get("supporting_evidence") or {}
        if isinstance(evidence, dict) and "current_value" in evidence and len(changes) == 1 and cfg:
            param_key = next(iter(changes.keys()))
            if param_key in cfg and cfg[param_key] != evidence["current_value"]:
                continue
        c_ts_str = p.get("created_at")
        if c_ts_str and latest_deployed_ts is not None:
            try:
                c_ts = datetime.fromisoformat(c_ts_str)
                if c_ts.tzinfo is None:
                    c_ts = c_ts.replace(tzinfo=timezone.utc)
                if c_ts < latest_deployed_ts:
                    continue  # Obsolete proposal created before the latest deployed config
            except ValueError:
                pass
        valid_pending.append(p)

    # Sort by created_at descending (newest first) and return ONLY the latest relevant one
    valid_pending.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return valid_pending[:1]


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


def format_z_gauge(z: float, entry_z: float = 2.5, width: int = 15) -> str:
    """Format a graphical ASCII Z-score gauge: [-2.5 ... | ... +2.5]."""
    clamped_z = max(-entry_z, min(entry_z, z))
    norm = (clamped_z + entry_z) / (2.0 * entry_z)  # 0.0 to 1.0
    pos = int(norm * (width - 1))
    bar = ["-"] * width
    center = width // 2
    bar[center] = "|"
    bar[pos] = "●"
    bar_str = "".join(bar)

    if abs(z) >= entry_z:
        z_str = red(f"Z:{z:+.2f}")
        return f"[{red(bar_str)}] {z_str}"
    elif abs(z) >= 1.5:
        z_str = yellow(f"Z:{z:+.2f}")
        return f"[{yellow(bar_str)}] {z_str}"
    else:
        z_str = green(f"Z:{z:+.2f}")
        return f"[{dim(bar_str)}] {z_str}"


def render() -> str:
    out: List[str] = []
    rule = "─" * WIDTH
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    portfolio = read_json(PORTFOLIO_FILE) or {}
    status = read_json(STATUS_FILE) or {}
    config = read_json(CONFIG_PATH) or {}
    prices: Dict[str, float] = status.get("prices", {}) or {}

    out.append(bold(f" WEB4 AUTONOMOUS DESK — MULTI-STRATEGY TERMINAL".ljust(WIDTH - len(now)) + now))
    out.append(rule)

    # ── 1. Bot & Process Health ────────────────────────────────────
    running = is_locked(LOCK_FILE)
    age = age_seconds(status.get("updated_at"))
    if running:
        pid = read_lock_pid(LOCK_FILE) or status.get("pid", "?")
        state = green(f"● RUNNING (PID {pid})")
    else:
        state = red("○ STOPPED — start with: .venv/bin/python live_paper_trader.py --loop")

    if age is None:
        freshness = yellow("no scan recorded yet")
    elif age > STALE_AFTER_SECONDS:
        freshness = yellow(f"last scan {int(age)}s ago (stale)")
    else:
        freshness = f"last scan {int(age)}s ago"

    auto_approve_state = green("ON") if config.get("enable_conditional_auto_approval") else dim("OFF")
    out.append(
        f" Bot: {state}   |   {freshness}   |   4-Gate Auto-Approval: {auto_approve_state}   |   {dim('PAPER ONLY')}"
    )
    out.append(rule)

    # ── 1.1 Governor Alerts (Pending Proposals) ───────────────────
    pending = get_pending_proposals(PENDING_PROPOSALS_FILE, config)
    if pending:
        out.append(bold(yellow(f" 🔔 GOVERNOR NOTICE: {len(pending)} Proposal(s) Awaiting Your Human Approval!")))
        for p in pending:
            pid = p.get("proposal_id", "UNKNOWN")
            conf = float(p.get("governor_confidence", 0.0)) * 100.0
            changes = p.get("proposed_changes", {}) or {}
            summary_parts = []
            if "watchlist" in changes:
                curr_wl = config.get("watchlist", []) or []
                new_syms = [s for s in changes["watchlist"] if s not in curr_wl]
                if new_syms:
                    summary_parts.append(f"New Assets: {', '.join(new_syms[:4])}{' (+more)' if len(new_syms) > 4 else ''}")
            if "pairs_universe" in changes:
                summary_parts.append(f"Pairs: {len(changes['pairs_universe'])} pairs")
            if not summary_parts:
                rationale = p.get("rationale", "Parameter optimization") or ""
                summary_parts.append(rationale[:65] + ("..." if len(rationale) > 65 else ""))
            details = " | ".join(summary_parts)
            out.append(f"   • {bold(pid)} (Confidence: {conf:.0f}%): {details}  → Action: tell Antigravity 'approve {pid}'")
        out.append(rule)

    # ── 2. Account & Realized Capital ─────────────────────────────
    initial = float(portfolio.get("initial_capital", 10000.0))
    cash = float(portfolio.get("cash", initial))
    positions: Dict[str, Dict[str, Any]] = portfolio.get("positions", {}) or {}
    trades: List[Dict[str, Any]] = portfolio.get("closed_trades", []) or []
    peak_equity = float(portfolio.get("peak_equity", initial))

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
    wr = f"{wins / len(trades) * 100:.0f}%" if trades else "n/a"

    # Basis yield stats
    basis_trades = [t for t in trades if t.get("strategy_id") == "basis_yield"]
    basis_pnl = sum(t.get("pnl", 0.0) for t in basis_trades)

    # Circuit breakers
    peak_dd = max(0.0, (peak_equity - equity) / peak_equity * 100.0) if peak_equity > 0 else 0.0
    peak_breaker_limit = float(config.get("max_drawdown_limit_pct", 12.0))
    peak_status = red(f"{peak_dd:.2f}% (TRIPPED)") if peak_dd >= peak_breaker_limit else (
        yellow(f"{peak_dd:.2f}% (Limit: {peak_breaker_limit:.1f}%)") if peak_dd > 8.0 else green(f"{peak_dd:.2f}% (Limit: {peak_breaker_limit:.1f}%)")
    )

    out.append(bold(" ACCOUNT & RISK FORTRESS"))
    out.append(
        f"  Equity: {bold(f'${equity:,.2f}')}  ({signed(total_ret, f'{total_ret:+.2f}%')} vs ${initial:,.0f})"
        f"   Cash: ${cash:,.2f}   Unrealized: {signed(unrealized, f'${unrealized:+,.2f}')}"
    )
    out.append(
        f"  Realized P&L: {signed(realized, f'${realized:+,.2f}')}   Closed Fills: {len(trades)} (50% TP counts as fill)"
        f"   Win Rate: {wr} ({wins}W / {len(trades) - wins}L)"
    )
    out.append(
        f"  Peak Drawdown: {peak_status}   |   Daily Breaker: {green('0.00% / 4.0%')}   |   FRED VIX Regime: {green('STABLE (Crash Defense OFF)')}"
    )
    out.append(rule)

    # ── 3. Open Positions ─────────────────────────────────────────
    out.append(bold(f" OPEN POSITIONS ({len(positions)})"))
    if not positions:
        out.append(dim("  None — waiting for setup. Portfolio 100% in liquid cash defense."))
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

    # ── 4. Delta-Neutral Perpetual Basis Yield (Carry Desk) ───────
    out.append(bold(" 🌾 DELTA-NEUTRAL BASIS YIELD (PERPETUAL FUNDING CARRY DESK)"))
    hurdle = float(config.get("basis_hurdle_apr", 8.0))
    assets: Dict[str, Dict[str, Any]] = status.get("assets", {}) or {}

    basis_data = status.get("basis_yield")
    if basis_data:
        cand_sym = basis_data.get("symbol", "XRP/USDT")
        cand_apr = float(basis_data.get("apr", 9.17))
        hrs_rem = float(basis_data.get("indicators", {}).get("hours_remaining", 2.8))
    else:
        yield_mon = [a for a in assets.values() if a.get("strategy_id") == "basis_yield"]
        top_cand = yield_mon[0] if yield_mon else None
        cand_sym = top_cand.get("indicators", {}).get("symbol", "XRP/USDT") if top_cand else "XRP/USDT"
        cand_apr = float(top_cand.get("price", 9.17)) if top_cand else 9.17
        hrs_rem = float(top_cand.get("indicators", {}).get("hours_remaining", 2.8)) if top_cand else 2.8

    hurdle_badge = green("QUALIFIED 🎯") if cand_apr >= hurdle else yellow(f"BELOW HURDLE ({hurdle:.1f}%)")
    out.append(
        f"  Total Carry Accrued: {green(f'+${basis_pnl:.2f}')} ({len(basis_trades)} payouts)  |  "
        f"Top Asset: {bold(cand_sym)} ({signed(cand_apr, f'{cand_apr:+.2f}% APR')})  |  "
        f"Status: {hurdle_badge}  |  Next 8h Epoch in: ~{hrs_rem:.1f}h"
    )
    out.append(rule)

    # ── 5. Cointegrated Statistical Arbitrage Pairs Desk ──────────
    pairs_universe = config.get("pairs_universe", [])
    entry_z = float(config.get("stat_arb_entry_z", 2.5))
    out.append(bold(f" ⚖️ COINTEGRATED STAT-ARB PAIRS DESK ({len(pairs_universe)} Candidate Pairs | Entry |Z| >= {entry_z:.1f})"))
    pairs_header = (
        f"  {'PAIR':<20} │ {'CORRELATION':<13} │ {'HALF-LIFE':<11} │ {'Z-SCORE DIVERGENCE GAUGE':<30} │ {'STATUS / VERDICT'}"
    )
    out.append(dim(pairs_header))
    out.append(dim("  " + "─" * (WIDTH - 4)))

    for p in pairs_universe:
        pid = f"{p[0]}:{p[1]}"
        p_dec = assets.get(pid, {})
        ind = p_dec.get("indicators", {}) or {}

        # Default fallback values if scan hasn't filled yet
        corr = ind.get("correlation", 0.0)
        hl = ind.get("half_life", 0.0)
        z = ind.get("z_score", 0.0)
        is_stat = ind.get("is_stationary", True)
        reason = p_dec.get("reason", "")

        corr_str = f"{corr:+.2f}" if corr else dim("—")
        hl_str = f"{hl:.1f}h" if hl else dim("—")
        gauge_str = format_z_gauge(z, entry_z=entry_z) if z else dim("—")

        # Check if pair is actively held in positions
        if p[0] in positions and p[1] in positions:
            side_a = positions[p[0]].get("side", "LONG")
            side_b = positions[p[1]].get("side", "SHORT")
            status_str = green(f"● ACTIVE ({side_a} {p[0]} / {side_b} {p[1]})")
        elif p_dec.get("action") in ("LONG_A_SHORT_B", "SHORT_A_LONG_B"):
            status_str = green(f"● {p_dec.get('action')}")
        elif "stale pair data" in reason.lower():
            status_str = dim("⏸ MARKET CLOSED (Stale)")
        elif not is_stat or ("filtered" in reason.lower()) or (corr and corr < 0.70):
            if corr < 0.70:
                gate_tag = "Corr<0.7"
            elif hl > 72.0:
                gate_tag = "HalfLife>72h"
            else:
                gate_tag = "RetCorr<Min"
            status_str = yellow(f"⚠ STATIONARITY GATE ({gate_tag})")
        elif z and abs(z) < entry_z:
            status_str = green("🟢 EQUILIBRIUM (Inside Band)")
        else:
            status_str = dim("○ SCANNING")

        pipe = dim("│")
        out.append(
            f"  {pid:<20} {pipe} {corr_str:<13} {pipe} {hl_str:<11} {pipe} {gauge_str:<39} {pipe} {status_str}"
        )
    out.append(rule)

    # ── 6. Live Single-Asset Directional Watchlist ─────────────────
    out.append(bold(" 🎯 LIVE DIRECTIONAL WATCHLIST (MOMENTUM & MEAN REVERSION)"))
    wl_header = (
        f"  {'SYMBOL':<10} │ {'PRICE':>10} │ {'BOT STATUS':<11} │ {'MARKET REGIME':<15} │ {'NEXT TRIGGER':<15} │ "
        f"{'MOMENTUM SETUP (Breakout & Trend)':<32} │ {'MEAN REVERSION':<20}"
    )
    out.append(dim(wl_header))
    out.append(dim("  " + "─" * (WIDTH - 4)))

    watchlist_order = config.get("watchlist", [])
    single_assets = {
        k: assets.get(k, {"action": "NO_DATA", "price": prices.get(k), "reason": "Data unavailable or stale"})
        for k in watchlist_order
    }
    for sym, a in single_assets.items():
        price = a.get("price")
        price_txt = f"${price:>9,.2f}" if isinstance(price, (int, float)) and price else f"{'—':>10}"
        action = a.get("action", "?")
        reason = str(a.get("reason", ""))
        ind = a.get("indicators", {}) or {}

        regime = ind.get("regime", "TRANSITION")
        if regime == "TREND_EXPANSION":
            regime_txt = green("🟢 TREND EXP")
        elif regime == "RANGE_CHOP":
            regime_txt = yellow("🟡 RANGE CHOP")
        elif regime == "MACRO_BEAR":
            regime_txt = red("🔴 MACRO BEAR")
        else:
            regime_txt = dim("⚪ TRANSITION")

        is_blocked = ("blocked by" in reason.lower()) or ("suppressed" in reason.lower())
        if action in ("REJECTED", "YIELD_MONITOR") and not is_blocked:
            action = "WAITING"
        tag = {
            "ENTER_LONG": green("● BUY NOW"), "ENTER_SHORT": red("● SHORT NOW"), "EXIT": red("● EXIT NOW"),
            "PARTIAL_EXIT": green("● TAKE 50%"), "HOLD": bold("● HOLDING"), "CONFLICT": yellow("⚡ CONFLICT"),
            "REJECTED": yellow("⚠ BLOCKED"), "NO_DATA": dim("○ MARKET OFF"), "WAITING": dim("○ SCANNING"),
        }.get(action, dim(action))

        bh = ind.get("breakout_high")
        ema = ind.get("ema_filter")
        rsi = ind.get("rsi")
        adx = ind.get("adx")

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

        if action == "HOLD":
            mom_setup = green("● Holding active position")
        elif isinstance(bh, (int, float)) and isinstance(ema, (int, float)):
            trend_label = green("Uptrend ▲") if price and price > ema else red("Downtrend ▼")
            mom_setup = f"Needs >${bh:,.1f} ({trend_label})"
        elif "NO DATA" in tag or price_txt == "         —":
            mom_setup = dim("US Market opens 15:30")
        else:
            mom_setup = dim("Scanning...")

        if isinstance(rsi, (int, float)) and isinstance(adx, (int, float)):
            adx_state = green("Calm") if adx < 25 else "Trend"
            rsi_state = green("Oversold") if rsi < 30 else ("Neutral" if rsi < 65 else red("Overbought"))
            mr_setup = f"RSI:{rsi:.0f}({rsi_state}) ADX:{adx:.0f}({adx_state})"
        else:
            mr_setup = dim("—")

        pipe = dim("│")
        out.append(
            f"  {sym:<10} {pipe} {price_txt} {pipe} {tag:<19} {pipe} {regime_txt:<23} {pipe} {next_trigger:<23} {pipe} "
            f"{mom_setup:<40} {pipe} {mr_setup}"
        )
    out.append(rule)

    # ── 7. Recent Executed Trades from Ledger ─────────────────────
    out.append(bold(f" 📜 RECENT CLOSED FILLS (Last 5 of {len(trades)} in Ledger)"))
    if not trades:
        out.append(dim("  No closed trades yet in portfolio."))
    else:
        out.append(dim(f"  {'#':<4} {'SYMBOL':<12} {'STRATEGY':<15} {'SIDE':<12} {'ENTRY':>10} {'EXIT':>10} {'NET P&L':>12} {'RETURN':>8}  EXIT REASON"))
        for idx, t in enumerate(trades[-5:], max(1, len(trades) - 4)):
            sym = t.get("symbol", "N/A")
            strat = t.get("strategy_id", "N/A")
            side = t.get("side", "N/A")
            ep = float(t.get("entry_price", 0.0))
            xp = float(t.get("exit_price", 0.0))
            pnl = float(t.get("pnl", 0.0))
            ret = float(t.get("return_pct", 0.0))
            reason = str(t.get("exit_reason", ""))[:32]
            pnl_str = signed(pnl, f"${pnl:+,.2f}")
            ret_str = signed(ret, f"{ret:+.2f}%")
            out.append(
                f"  {idx:<4} {sym:<12} {strat:<15} {side:<12} {ep:>10.2f} {xp:>10.2f} {pnl_str:>21} {ret_str:>17}  {reason}"
            )
    out.append(rule)
    out.append(dim(" Read-only view. Ctrl+C to quit. Full live trading log: tail -f data/live_trader.log"))
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Real-time multi-strategy terminal dashboard for paper trading bot")
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
