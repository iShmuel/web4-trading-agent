"""Zero-dependency, read-only Web Dashboard for the Web4 Trading Desk.

Serves a modern dark-theme browser UI and JSON API from standard-library
http.server without ever modifying paper_portfolio.json or active_config.json.

Usage:
    .venv/bin/python web_dashboard.py                  # http://127.0.0.1:8080
    .venv/bin/python web_dashboard.py --port 8090      # custom port
    .venv/bin/python web_dashboard.py --host 0.0.0.0   # LAN/VPN access
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.journal import summarize_maker_telemetry  # noqa: E402
from core.process_lock import is_locked, read_lock_pid  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = PROJECT_ROOT / "config" / "active_config.json"
PORTFOLIO_FILE = DATA_DIR / "paper_portfolio.json"
STATUS_FILE = DATA_DIR / "live_status.json"
LOCK_FILE = DATA_DIR / "live_trader.lock"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists() and path.stat().st_size > 0:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return None
    return None


def _classify_watchlist_row(sym: str, action_obj: Dict[str, Any], price: Optional[float]) -> Dict[str, Any]:
    action = str(action_obj.get("action", "WAITING"))
    reason = str(action_obj.get("reason", ""))
    ind = action_obj.get("indicators", {}) or {}
    rl = reason.lower()
    is_blocked = ("blocked by" in rl) or ("suppressed" in rl)
    if action in ("REJECTED", "YIELD_MONITOR") and not is_blocked:
        action = "SCANNING"

    if action == "HOLD":
        strategy_label = str(action_obj.get("strategy_id", "Active"))
        trigger = f"Stop ${float(action_obj.get('stop_loss', 0.0)):,.2f}"
    elif "selected 'volatility_squeeze'" in rl:
        strategy_label, trigger = "Vol Squeeze", "Vol Squeeze"
    elif "selected 'trend_pullback'" in rl:
        strategy_label, trigger = "Trend Pullback", "Trend Pullback"
    elif "selected 'vwap_reversion'" in rl:
        strategy_label, trigger = "VWAP Reversion", "VWAP Reversion"
    elif "selected 'mean_reversion'" in rl:
        strategy_label, trigger = "Mean Reversion", "Mean Reversion"
    elif "selected 'momentum'" in rl or "no breakout" in rl or "macro_bear" in rl:
        strategy_label = "Momentum"
        trigger = reason[:56] if reason else "Momentum"
    elif "selected 'basis_yield'" in rl:
        fr = ind.get("funding_rate_pct")
        strategy_label = "Basis Yield"
        trigger = f"Carry FR {fr:+.3f}%" if isinstance(fr, (int, float)) else "Basis Yield"
    elif "selected 'cash'" in rl:
        strategy_label, trigger = "Stand Down", "Cash (PF < 1.15)"
    elif "stale data" in rl:
        strategy_label, trigger = "Market Closed", "Stale / Weekend"
    elif "blacklisted" in rl or "asset pruned" in rl:
        strategy_label, trigger = "Blacklisted", "Quarantined"
    else:
        strategy_label = str(action_obj.get("strategy_id", "Scanning"))
        trigger = reason[:36] if reason else "Scanning"

    return {
        "symbol": sym,
        "price": price,
        "status": action,
        "regime": str(ind.get("regime", "TRANSITION")),
        "strategy": strategy_label,
        "trigger": trigger,
        "reason": reason,
    }


def build_dashboard_payload(
    status_path: Optional[Path] = None,
    portfolio_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
    lock_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Assemble read-only JSON payload for the web dashboard."""
    st_file = status_path or STATUS_FILE
    pf_file = portfolio_path or PORTFOLIO_FILE
    cfg_file = config_path or CONFIG_PATH
    lk_file = lock_path or LOCK_FILE

    status = _read_json(st_file) or {}
    portfolio = _read_json(pf_file) or {}
    cfg = _read_json(cfg_file) or {}

    closed = list(portfolio.get("closed_trades", []))
    wins = sum(1 for t in closed if float(t.get("pnl", 0.0)) > 0)
    realized = round(sum(float(t.get("pnl", 0.0)) for t in closed), 2)
    initial_cap = float(portfolio.get("initial_capital", 10000.0))

    first_ts = str(closed[0].get("entry_time", ""))[5:16].replace("T", " ") if closed else "Start"
    eq_curve: List[float] = [round(initial_cap, 2)]
    eq_points: List[Dict[str, Any]] = [{"eq": round(initial_cap, 2), "ts": first_ts or "Start", "label": f"Start ${initial_cap:,.0f}", "pnl": 0.0}]
    running = initial_cap
    for idx, t in enumerate(closed, start=1):
        pnl_t = float(t.get("pnl", 0.0))
        running += pnl_t
        eq_val = round(running, 2)
        eq_curve.append(eq_val)
        sym_t = str(t.get("symbol", "Trade"))
        sign_t = "+" if pnl_t >= 0 else ""
        ts_t = str(t.get("exit_time") or t.get("entry_time") or "")[5:16].replace("T", " ")
        eq_points.append({"eq": eq_val, "ts": ts_t, "label": f"#{idx} {sym_t} {sign_t}${pnl_t:,.2f}" + (f" ({ts_t})" if ts_t else ""), "pnl": round(pnl_t, 2)})
    if isinstance(status.get("equity"), (int, float)):
        live_eq = round(float(status["equity"]), 2)
        now_ts = str(status.get("updated_at") or datetime.now(timezone.utc).isoformat())[5:16].replace("T", " ")
        eq_curve.append(live_eq)
        eq_points.append({"eq": live_eq, "ts": now_ts, "label": f"Live ${live_eq:,.2f} ({now_ts})", "pnl": None})

    prices = status.get("prices", {}) or {}
    actions = status.get("assets") or status.get("actions") or {}
    bl_set = set(cfg.get("blacklisted_symbols", []))
    watchlist = [s for s in cfg.get("watchlist", []) if s not in bl_set]
    rows = [_classify_watchlist_row(sym, actions.get(sym, {}), prices.get(sym)) for sym in watchlist]

    pairs_stop_counts = portfolio.get("pair_stop_counts", {}) or {}
    pairs_cooldowns = portfolio.get("pair_cooldown_until", {}) or {}
    pairs_blacklist = list(cfg.get("pairs_Blacklist", cfg.get("blacklisted_pairs", [])))

    pairs_rows = []
    for p in (cfg.get("pairs_universe") or []):
        if isinstance(p, (list, tuple)) and len(p) == 2:
            pid = f"{p[0]}:{p[1]}"
            p_dec = actions.get(pid, {}) or {}
            ind = p_dec.get("indicators", {}) or {}
            pairs_rows.append({
                "pair": pid,
                "correlation": round(float(ind.get("correlation", 0.0)), 2),
                "half_life": round(float(ind.get("half_life", 0.0)), 1),
                "z_score": round(float(ind.get("z_score", 0.0)), 2),
                "verdict": str(p_dec.get("reason") or p_dec.get("action") or "Scanning"),
                "strikes": int(pairs_stop_counts.get(pid, 0)),
                "cooldown_until": pairs_cooldowns.get(pid),
            })

    equity_val = float(status.get("equity", portfolio.get("cash", initial_cap)))
    open_positions = []
    total_open_notional = 0.0
    for sym, pos in (portfolio.get("positions", {}) or {}).items():
        cur_px = float(prices.get(sym, pos.get("entry_price", 0.0)))
        ep = float(pos.get("entry_price", 0.0))
        units = float(pos.get("units", 0.0))
        side = str(pos.get("side", "LONG")).upper()
        notional = abs(cur_px * units)
        total_open_notional += notional
        upnl = ((ep - cur_px) * units) if side == "SHORT" else ((cur_px - ep) * units)
        ret_pct = ((ep - cur_px) / ep * 100.0) if (side == "SHORT" and ep > 0) else (((cur_px - ep) / ep * 100.0) if ep > 0 else 0.0)
        stop_px = float(pos.get("trailing_stop", 0.0))
        stop_dist_pct = (
            ((stop_px - cur_px) / cur_px * 100.0) if (side == "SHORT" and cur_px > 0 and stop_px > 0)
            else (((cur_px - stop_px) / cur_px * 100.0) if (cur_px > 0 and stop_px > 0) else 0.0)
        )
        open_positions.append({
            "symbol": sym,
            "strategy_id": pos.get("strategy_id", "default"),
            "side": side,
            "units": round(units, 4),
            "entry_price": round(ep, 4),
            "current_price": round(cur_px, 4),
            "trailing_stop": round(stop_px, 4),
            "notional": round(notional, 2),
            "exposure_pct": round((notional / equity_val * 100.0) if equity_val > 0 else 0.0, 1),
            "return_pct": round(ret_pct, 2),
            "stop_dist_pct": round(stop_dist_pct, 2),
            "unrealized_pnl": round(upnl, 2),
        })

    strat_agg: Dict[str, Dict[str, Any]] = {}
    gross_profit_all = 0.0
    gross_loss_all = 0.0
    total_fees_all = 0.0
    for t in closed:
        sid = str(t.get("strategy_id") or "unknown")
        pnl_v = float(t.get("pnl", 0.0))
        fee_v = float(t.get("fees", 0.0))
        total_fees_all += fee_v
        if pnl_v > 0:
            gross_profit_all += pnl_v
        elif pnl_v < 0:
            gross_loss_all += abs(pnl_v)
        bucket = strat_agg.setdefault(sid, {"strategy_id": sid, "trades": 0, "wins": 0, "gross_profit": 0.0, "gross_loss": 0.0, "net_pnl": 0.0, "fees": 0.0})
        bucket["trades"] += 1
        if pnl_v > 0:
            bucket["wins"] += 1
            bucket["gross_profit"] += pnl_v
        elif pnl_v < 0:
            bucket["gross_loss"] += abs(pnl_v)
        bucket["net_pnl"] += pnl_v
        bucket["fees"] += fee_v

    strategy_breakdown = []
    for b in strat_agg.values():
        pf = (b["gross_profit"] / b["gross_loss"]) if b["gross_loss"] > 0 else (99.9 if b["gross_profit"] > 0 else 0.0)
        strategy_breakdown.append({
            "strategy_id": b["strategy_id"],
            "trades": b["trades"],
            "wins": b["wins"],
            "win_rate_pct": round((b["wins"] / b["trades"] * 100.0) if b["trades"] else 0.0, 1),
            "profit_factor": round(pf, 2),
            "fees": round(b["fees"], 2),
            "net_pnl": round(b["net_pnl"], 2),
        })
    strategy_breakdown.sort(key=lambda x: x["net_pnl"], reverse=True)
    overall_pf = (gross_profit_all / gross_loss_all) if gross_loss_all > 0 else (99.9 if gross_profit_all > 0 else 0.0)

    bot_running = is_locked(lk_file)
    bot_pid = read_lock_pid(lk_file) if bot_running else None
    updated_at = status.get("updated_at")
    scan_age_sec: Optional[int] = None
    if updated_at:
        try:
            dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
            scan_age_sec = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
        except Exception:
            scan_age_sec = None

    by_status = status.get("basis_yield") or {}
    by_ind = by_status.get("indicators", {}) or {}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bot": {
            "running": bot_running,
            "pid": bot_pid,
            "scan_age_sec": scan_age_sec,
            "auto_approval": bool(cfg.get("enable_conditional_auto_approval", False)),
            "paper_only": True,
            "fred_vix_regime": str(status.get("fred_vix_regime", "STABLE (Crash Defense OFF)")),
        },
        "account": {
            "initial_capital": round(initial_cap, 2),
            "equity": round(equity_val, 2),
            "return_vs_initial_pct": round(((equity_val - initial_cap) / initial_cap * 100.0) if initial_cap > 0 else 0.0, 2),
            "cash": round(float(portfolio.get("cash", 0.0)), 2),
            "unrealized_pnl": round(sum(p["unrealized_pnl"] for p in open_positions), 2),
            "realized_pnl": realized,
            "closed_trades": len(closed),
            "wins": wins,
            "losses": len(closed) - wins,
            "win_rate_pct": round((wins / len(closed) * 100.0) if closed else 0.0, 1),
            "drawdown_pct": round(float(portfolio.get("current_drawdown", 0.0)) * 100.0, 2),
            "peak_dd_limit_pct": float(cfg.get("max_drawdown_limit_pct", 12.0)),
            "circuit_breaker": str(portfolio.get("circuit_breaker_status", "OK")),
        },
        "basis_yield": {
            "total_carry_usd": round(float(portfolio.get("basis_yield_total_usd", 0.0)), 2),
            "payouts": int(portfolio.get("basis_yield_payouts", 0)),
            "hurdle_apr": float(cfg.get("basis_hurdle_apr", 5.0)),
            "top_symbol": str(by_status.get("symbol", "—")),
            "top_apr": round(float(by_status.get("apr", by_ind.get("apr", 0.0))), 2),
            "next_epoch_hours": round(float(by_ind.get("hours_remaining", 0.0)), 1),
        },
        "maker_telemetry": summarize_maker_telemetry(),
        "risk_telemetry": {
            "open_notional": round(total_open_notional, 2),
            "open_exposure_pct": round((total_open_notional / equity_val * 100.0) if equity_val > 0 else 0.0, 1),
            "open_count": len(open_positions),
            "max_open_positions": int(cfg.get("max_concurrent_positions", 5)),
            "peak_equity": round(float(portfolio.get("peak_equity", equity_val)), 2),
            "total_fees": round(total_fees_all, 2),
            "profit_factor": round(overall_pf, 2),
            "blacklisted_symbols_count": len(bl_set),
            "blacklisted_pairs": pairs_blacklist,
            "pair_stop_counts": pairs_stop_counts,
        },
        "strategy_breakdown": strategy_breakdown,
        "open_positions": open_positions,
        "watchlist": rows,
        "pairs": pairs_rows,
        "recent_trades": list(reversed(closed[-15:])),
        "equity_curve": eq_curve[-120:],
        "equity_points": eq_points[-120:],
    }


HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Web4 Autonomous Trading Desk</title>
<style>
  :root { --bg:#0b1120; --card:#111827; --border:#1f2937; --text:#e5e7eb; --muted:#9ca3af; --green:#10b981; --red:#ef4444; --cyan:#38bdf8; --amber:#f59e0b; --purple:#a78bfa; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--text); }
  .wrap { max-width:1360px; margin:0 auto; padding:20px; }
  .topbar { display:flex; flex-wrap:wrap; gap:12px; justify-content:space-between; align-items:center; background:var(--card); border:1px solid var(--border); border-radius:12px; padding:14px 18px; margin-bottom:16px; }
  .title { font-size:18px; font-weight:700; color:var(--cyan); }
  .badges { display:flex; flex-wrap:wrap; gap:8px; font-size:12px; }
  .badge { padding:4px 10px; border-radius:999px; background:#1e293b; border:1px solid var(--border); }
  .ok { color:var(--green); border-color:rgba(16,185,129,.35); }
  .warn { color:var(--amber); border-color:rgba(245,158,11,.35); }
  .bad { color:var(--red); border-color:rgba(239,68,68,.35); }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px; margin-bottom:16px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:14px 16px; }
  .k { font-size:12px; color:var(--muted); margin-bottom:6px; }
  .v { font-size:22px; font-weight:700; }
  .sub { font-size:12px; color:var(--muted); margin-top:4px; }
  .section { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px; margin-bottom:16px; overflow-x:auto; }
  .sec-head { display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:10px; margin-bottom:12px; }
  h2 { margin:0; font-size:15px; color:var(--cyan); }
  .controls { display:flex; gap:8px; }
  input, select { background:#0f172a; color:var(--text); border:1px solid var(--border); border-radius:8px; padding:6px 10px; font-size:13px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); white-space:nowrap; }
  th { color:var(--muted); font-weight:600; font-size:12px; }
  .pill { padding:2px 8px; border-radius:6px; font-size:12px; font-weight:600; background:#1e293b; }
  .p-green { color:var(--green); background:rgba(16,185,129,.12); }
  .p-cyan { color:var(--cyan); background:rgba(56,189,248,.12); }
  .p-purple { color:var(--purple); background:rgba(167,139,250,.12); }
  .p-muted { color:var(--muted); }
  .zbar { position:relative; display:inline-block; width:110px; height:8px; background:#1e293b; border-radius:4px; vertical-align:middle; margin-right:6px; }
  .zbar-mid { position:absolute; left:50%; top:-2px; width:1px; height:12px; background:#475569; }
  .zbar-dot { position:absolute; top:-1px; width:8px; height:10px; border-radius:3px; transform:translateX(-50%); }
  svg { width:100%; height:130px; display:block; overflow:visible; }
  .eq-wrap { position:relative; }
  .eq-dot { cursor:pointer; transition:r .1s; }
  .eq-dot:hover { r:5.5; }
  #eq-tip { position:fixed; display:none; pointer-events:none; background:#0f172a; border:1px solid var(--cyan); color:var(--text); padding:4px 9px; border-radius:6px; font-size:12px; font-weight:600; z-index:99; white-space:nowrap; box-shadow:0 4px 12px rgba(0,0,0,.5); }
  .two-col { display:grid; grid-template-columns:repeat(auto-fit,minmax(460px,1fr)); gap:16px; margin-bottom:16px; }
  .two-col .section { margin-bottom:0; }
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="title">WEB4 AUTONOMOUS TRADING DESK — LIVE WEB TERMINAL</div>
    <div class="badges" id="top-badges">Loading...</div>
  </div>

  <div class="grid" id="kpi-grid"></div>

  <div class="two-col">
    <div class="section">
      <div class="sec-head"><h2>Portfolio Equity Curve (Realized Ledger + Live Mark)</h2><span class="sub" id="eq-range"></span></div>
      <div class="eq-wrap"><svg id="eq-svg" viewBox="0 0 800 130" preserveAspectRatio="none"></svg></div>
    </div>
    <div class="section">
      <div class="sec-head"><h2>Strategy Performance Breakdown</h2><span class="sub" id="strat-sub"></span></div>
      <table><thead><tr><th>Strategy</th><th>Trades</th><th>Win Rate</th><th>Profit Factor</th><th>Fees Paid</th><th>Net P&amp;L</th></tr></thead><tbody id="strat-body"></tbody></table>
    </div>
  </div>
  <div id="eq-tip"></div>

  <div class="section">
    <div class="sec-head"><h2>Open Positions</h2><span class="sub" id="open-sub"></span></div>
    <table><thead><tr><th>Symbol</th><th>Strategy</th><th>Side</th><th>Units</th><th>Notional</th><th>Entry</th><th>Now</th><th>Stop (Dist)</th><th>Unrealized P&amp;L</th></tr></thead><tbody id="open-body"></tbody></table>
  </div>

  <div class="section">
    <div class="sec-head">
      <h2>Live Watchlist (Web4 Dynamic Rolling Router)</h2>
      <div class="controls">
        <input id="wl-search" placeholder="Search symbol..." />
        <select id="wl-filter"><option value="">All Strategies</option><option>Vol Squeeze</option><option>Trend Pullback</option><option>VWAP Reversion</option><option>Mean Reversion</option><option>Momentum</option><option>Basis Yield</option><option>Stand Down</option><option>Market Closed</option></select>
      </div>
    </div>
    <table><thead><tr><th>Symbol</th><th>Price</th><th>Status</th><th>Regime</th><th>Active Strategy</th><th>Trigger / Info</th></tr></thead><tbody id="wl-body"></tbody></table>
  </div>

  <div class="section">
    <div class="sec-head"><h2>Cointegrated Stat-Arb Pairs Desk</h2><span class="sub" id="pairs-sub"></span></div>
    <table><thead><tr><th>Pair</th><th>Correlation</th><th>Half-Life</th><th>Z-Score Divergence</th><th>Strikes / Guard</th><th>Status / Verdict</th></tr></thead><tbody id="pairs-body"></tbody></table>
  </div>

  <div class="section">
    <div class="sec-head">
      <h2>Recent Closed Fills</h2>
      <div class="controls">
        <select id="closed-filter"><option value="">All Strategies</option><option>momentum</option><option>mean_reversion</option><option>stat_arb_pairs</option><option>basis_yield</option><option>volatility_squeeze</option><option>trend_pullback</option><option>vwap_reversion</option></select>
      </div>
    </div>
    <table><thead><tr><th>Time (UTC)</th><th>Symbol</th><th>Strategy</th><th>Side</th><th>Entry</th><th>Exit</th><th>Fees</th><th>Net P&amp;L</th><th>Return</th><th>Reason</th></tr></thead><tbody id="closed-body"></tbody></table>
  </div>
</div>
<script>
let lastData = null;
const fmt = (n, d=2) => (n === null || n === undefined) ? '—' : Number(n).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
function stratPill(s){
  if(['Vol Squeeze','Trend Pullback','VWAP Reversion'].includes(s)) return `<span class="pill p-green">${s}</span>`;
  if(s==='Basis Yield') return `<span class="pill p-cyan">${s}</span>`;
  if(['Mean Reversion','Momentum'].includes(s)) return `<span class="pill p-purple">${s}</span>`;
  return `<span class="pill p-muted">${s}</span>`;
}
function renderWatchlist(){
  if(!lastData) return;
  const q = document.getElementById('wl-search').value.trim().toUpperCase();
  const f = document.getElementById('wl-filter').value;
  const rows = lastData.watchlist.filter(r => (!q || r.symbol.includes(q)) && (!f || r.strategy === f));
  document.getElementById('wl-body').innerHTML = rows.map(r => `<tr>
    <td><b>${r.symbol}</b></td><td>${r.price !== null ? '$'+fmt(r.price, r.price < 1 ? 5 : 2) : '—'}</td>
    <td>${r.status}</td><td>${r.regime}</td><td>${stratPill(r.strategy)}</td><td>${r.trigger}</td>
  </tr>`).join('') || '<tr><td colspan="6" class="p-muted">No matching symbols</td></tr>';
}
function showEqTip(e, txt){
  const tip = document.getElementById('eq-tip');
  tip.textContent = txt;
  tip.style.display = 'block';
  tip.style.left = Math.min(window.innerWidth - 220, e.clientX + 10) + 'px';
  tip.style.top = Math.max(8, e.clientY - 30) + 'px';
}
function hideEqTip(){ document.getElementById('eq-tip').style.display = 'none'; }
function renderCurve(pts, metaPts){
  const svg = document.getElementById('eq-svg');
  if(!pts || pts.length < 2){ svg.innerHTML = ''; return; }
  const min = Math.min(...pts), max = Math.max(...pts), span = (max - min) || 1;
  document.getElementById('eq-range').textContent = `Min $${fmt(min)} | Max $${fmt(max)} | Now $${fmt(pts[pts.length-1])}`;
  const x0 = 58, x1 = 790, y0 = 12, y1 = 106, w = x1 - x0, h = y1 - y0;
  const hGrid = [0, 0.5, 1].map(f => {
    const y = (y0 + f * h).toFixed(1);
    const val = max - f * span;
    return `<line x1="${x0}" y1="${y}" x2="${x1}" y2="${y}" stroke="#1f2937" stroke-dasharray="3,3" /><text x="52" y="${Number(y)+3}" text-anchor="end" fill="#9ca3af" font-size="10">$${fmt(val,0)}</text>`;
  }).join('');
  const xTicks = [0, 0.25, 0.5, 0.75, 1].map(f => {
    const idx = Math.min(pts.length - 1, Math.round(f * (pts.length - 1)));
    const x = (x0 + (idx / (pts.length - 1)) * w).toFixed(1);
    const ts = (metaPts && metaPts[idx] && metaPts[idx].ts) ? metaPts[idx].ts : `#${idx+1}`;
    const anchor = f === 0 ? 'start' : (f === 1 ? 'end' : 'middle');
    return `<line x1="${x}" y1="${y0}" x2="${x}" y2="${y1}" stroke="#1e293b" stroke-dasharray="2,3" /><text x="${x}" y="124" text-anchor="${anchor}" fill="#9ca3af" font-size="10">${ts}</text>`;
  }).join('');
  const xy = pts.map((v,i) => [Number((x0 + (i/(pts.length-1))*w).toFixed(1)), Number((y1 - ((v-min)/span)*h).toFixed(1))]);
  const coords = xy.map(p => `${p[0]},${p[1]}`).join(' ');
  const dots = xy.map((p, i) => {
    const m = (metaPts && metaPts[i]) || {label: `$${fmt(pts[i])}`, pnl: null};
    const fill = m.pnl === null ? '#38bdf8' : (m.pnl > 0 ? '#10b981' : (m.pnl < 0 ? '#ef4444' : '#38bdf8'));
    const safeTxt = String(m.label || '').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    return `<circle class="eq-dot" cx="${p[0]}" cy="${p[1]}" r="3" fill="${fill}" onmouseenter="showEqTip(event,'${safeTxt}')" onmousemove="showEqTip(event,'${safeTxt}')" onmouseleave="hideEqTip()"><title>${safeTxt}</title></circle>`;
  }).join('');
  svg.innerHTML = `${hGrid}${xTicks}<polyline fill="none" stroke="#38bdf8" stroke-width="2" points="${coords}" />${dots}`;
}
function render(d){
  lastData = d;
  document.getElementById('top-badges').innerHTML = `
    <span class="badge ${d.bot.running ? 'ok':'bad'}">${d.bot.running ? 'RUNNING PID '+d.bot.pid : 'STOPPED'}</span>
    <span class="badge">Scan: ${d.bot.scan_age_sec !== null ? d.bot.scan_age_sec+'s ago' : '—'}</span>
    <span class="badge ok">PAPER ONLY</span>
    <span class="badge ok">VIX: ${d.bot.fred_vix_regime}</span>
    <span class="badge">${d.bot.auto_approval ? '4-Gate Auto-Approval: ON':'Manual Approval'}</span>`;
  const a = d.account, by = d.basis_yield, mt = d.maker_telemetry, rt = d.risk_telemetry || {};
  document.getElementById('kpi-grid').innerHTML = `
    <div class="card"><div class="k">Total Equity</div><div class="v">$${fmt(a.equity)} <span class="${a.return_vs_initial_pct>=0?'ok':'bad'}" style="font-size:13px">(${a.return_vs_initial_pct>=0?'+':''}${fmt(a.return_vs_initial_pct)}% vs $${fmt(a.initial_capital,0)})</span></div><div class="sub">Cash: $${fmt(a.cash)} | Unrealized: <span class="${a.unrealized_pnl>=0?'ok':'bad'}">$${fmt(a.unrealized_pnl)}</span> | Peak: $${fmt(rt.peak_equity ?? a.equity)}</div></div>
    <div class="card"><div class="k">Open Exposure &amp; Risk</div><div class="v">${rt.open_count ?? 0}/${rt.max_open_positions ?? 5}</div><div class="sub">${fmt(rt.open_exposure_pct ?? 0, 1)}% deployed ($${fmt(rt.open_notional ?? 0)})</div></div>
    <div class="card"><div class="k">Drawdown &amp; Breaker</div><div class="v ${a.circuit_breaker==='OK'?'ok':'bad'}">${a.drawdown_pct}% <span class="p-muted" style="font-size:13px">/ ${fmt(a.peak_dd_limit_pct ?? 12,1)}%</span></div><div class="sub">Breaker: ${a.circuit_breaker} | Pruned: ${rt.blacklisted_symbols_count ?? 0}</div></div>
    <div class="card"><div class="k">Closed Fills (${a.closed_trades})</div><div class="v ${a.realized_pnl>=0?'ok':'bad'}">$${fmt(a.realized_pnl)}</div><div class="sub">WR: ${a.win_rate_pct}% (${a.wins}W / ${a.losses}L) | PF: ${fmt(rt.profit_factor ?? 0)} | Fees: $${fmt(rt.total_fees ?? 0)}</div></div>
    <div class="card"><div class="k">Basis Yield Carry</div><div class="v ok">+$${fmt(by.total_carry_usd)}</div><div class="sub">${by.payouts} payouts | Top: ${by.top_symbol} (${fmt(by.top_apr)}% / ${by.hurdle_apr}%) | Next: ${by.next_epoch_hours}h</div></div>
    <div class="card"><div class="k">Maker Telemetry</div><div class="v">${mt.fill_rate_pct}%</div><div class="sub">${mt.filled}/${mt.total} fills | ${mt.avg_slippage_bps} bps slip</div></div>`;
  renderCurve(d.equity_curve, d.equity_points);
  document.getElementById('open-sub').textContent = `${d.open_positions.length} open | $${fmt(rt.open_notional ?? 0)} notional (${fmt(rt.open_exposure_pct ?? 0, 1)}% eq)`;
  document.getElementById('open-body').innerHTML = d.open_positions.map(p => `<tr>
    <td><b>${p.symbol}</b></td><td>${p.strategy_id}</td><td>${p.side}</td><td>${p.units}</td>
    <td>$${fmt(p.notional)} (${fmt(p.exposure_pct,1)}%)</td>
    <td>$${fmt(p.entry_price, p.entry_price < 1 ? 4 : 2)}</td><td>$${fmt(p.current_price, p.current_price < 1 ? 4 : 2)}</td>
    <td>$${fmt(p.trailing_stop, p.trailing_stop < 1 ? 4 : 2)} (${fmt(p.stop_dist_pct, 1)}%)</td>
    <td class="${p.unrealized_pnl>=0?'ok':'bad'}">$${fmt(p.unrealized_pnl)} (${p.return_pct>=0?'+':''}${fmt(p.return_pct)}%)</td>
  </tr>`).join('') || '<tr><td colspan="9" class="p-muted">No open positions</td></tr>';
  const sb = d.strategy_breakdown || [];
  document.getElementById('strat-sub').textContent = `${sb.length} strategies with closed fills`;
  document.getElementById('strat-body').innerHTML = sb.map(s => `<tr>
    <td><b>${s.strategy_id}</b></td><td>${s.trades} (${s.wins}W)</td><td>${fmt(s.win_rate_pct,1)}%</td>
    <td>${fmt(s.profit_factor)}</td><td>$${fmt(s.fees)}</td>
    <td class="${s.net_pnl>=0?'ok':'bad'}">$${fmt(s.net_pnl)}</td>
  </tr>`).join('') || '<tr><td colspan="6" class="p-muted">No strategy fills yet</td></tr>';
  renderWatchlist();
  const blPairs = rt.blacklisted_pairs || [];
  document.getElementById('pairs-sub').textContent = `${(d.pairs||[]).length} active pairs | Blacklisted: ${blPairs.length ? blPairs.join(', ') : 'None'}`;
  document.getElementById('pairs-body').innerHTML = (d.pairs || []).map(p => {
    const z = Number(p.z_score || 0);
    const pct = Math.max(4, Math.min(96, 50 + (z / 3.5) * 45));
    const col = Math.abs(z) >= 2.5 ? 'var(--amber)' : 'var(--cyan)';
    const guard = p.cooldown_until ? `<span class="pill warn">Cooldown</span>` : (p.strikes > 0 ? `<span class="pill warn">${p.strikes}/2 strikes</span>` : `<span class="pill p-green">Clear (0/2)</span>`);
    return `<tr>
      <td><b>${p.pair}</b></td><td>${p.correlation >= 0 ? '+':''}${fmt(p.correlation)}</td>
      <td>${fmt(p.half_life, 1)}h</td>
      <td><span class="zbar"><span class="zbar-mid"></span><span class="zbar-dot" style="left:${pct.toFixed(1)}%;background:${col}"></span></span>${z >= 0 ? '+':''}${fmt(z)}</td>
      <td>${guard}</td><td>${p.verdict}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="6" class="p-muted">No pairs configured</td></tr>';
  renderClosed();
}
function renderClosed(){
  if(!lastData) return;
  const cf = document.getElementById('closed-filter').value;
  const rows = (lastData.recent_trades || []).filter(t => !cf || (t.strategy_id || '') === cf);
  document.getElementById('closed-body').innerHTML = rows.map(t => {
    const ts = t.exit_time ? String(t.exit_time).replace('T',' ').slice(0,16) : '—';
    return `<tr>
      <td class="p-muted">${ts}</td><td><b>${t.symbol}</b></td><td>${t.strategy_id||'—'}</td><td>${t.side}</td>
      <td>$${fmt(t.entry_price,4)}</td><td>$${fmt(t.exit_price,4)}</td><td>$${fmt(t.fees ?? 0)}</td>
      <td class="${t.pnl>=0?'ok':'bad'}">$${fmt(t.pnl)}</td><td>${fmt(t.return_pct)}%</td><td>${t.exit_reason||''}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="10" class="p-muted">No matching closed trades</td></tr>';
}
async function poll(){ try{ const r = await fetch('/api/state'); if(r.ok) render(await r.json()); }catch(e){} }
document.getElementById('wl-search').addEventListener('input', renderWatchlist);
document.getElementById('wl-filter').addEventListener('change', renderWatchlist);
document.getElementById('closed-filter').addEventListener('change', renderClosed);
poll(); setInterval(poll, 3000);
</script>
</body>
</html>"""


class WebDashboardHandler(BaseHTTPRequestHandler):
    """Read-only HTTP request handler for HTML UI, JSON state, and health check."""

    def do_GET(self) -> None:
        if self.path == "/health":
            body = json.dumps({"status": "ok"}).encode("utf-8")
            self._send(HTTPStatus.OK, "application/json", body)
        elif self.path == "/api/state":
            body = json.dumps(build_dashboard_payload()).encode("utf-8")
            self._send(HTTPStatus.OK, "application/json", body)
        elif self.path in ("/", "/index.html"):
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", HTML_PAGE.encode("utf-8"))
        else:
            self._send(HTTPStatus.NOT_FOUND, "application/json", b'{"error":"not_found"}')

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Web4 Read-Only Browser Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), WebDashboardHandler)
    print(f"[WebDashboard] Read-only UI running at http://{args.host}:{args.port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
