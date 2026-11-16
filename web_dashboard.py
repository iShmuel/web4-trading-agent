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

    eq_curve: List[float] = [round(initial_cap, 2)]
    running = initial_cap
    for t in closed:
        running += float(t.get("pnl", 0.0))
        eq_curve.append(round(running, 2))
    if isinstance(status.get("equity"), (int, float)):
        eq_curve.append(round(float(status["equity"]), 2))

    prices = status.get("prices", {}) or {}
    actions = status.get("assets") or status.get("actions") or {}
    bl_set = set(cfg.get("blacklisted_symbols", []))
    watchlist = [s for s in cfg.get("watchlist", []) if s not in bl_set]
    rows = [_classify_watchlist_row(sym, actions.get(sym, {}), prices.get(sym)) for sym in watchlist]

    open_positions = []
    for sym, pos in (portfolio.get("positions", {}) or {}).items():
        cur_px = prices.get(sym, pos.get("entry_price", 0.0))
        ep = float(pos.get("entry_price", 0.0))
        units = float(pos.get("units", 0.0))
        side = str(pos.get("side", "LONG")).upper()
        upnl = ((ep - float(cur_px)) * units) if side == "SHORT" else ((float(cur_px) - ep) * units)
        open_positions.append({
            "symbol": sym,
            "strategy_id": pos.get("strategy_id", "default"),
            "side": side,
            "units": round(units, 4),
            "entry_price": round(ep, 4),
            "current_price": round(float(cur_px), 4),
            "trailing_stop": round(float(pos.get("trailing_stop", 0.0)), 4),
            "unrealized_pnl": round(upnl, 2),
        })

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

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bot": {
            "running": bot_running,
            "pid": bot_pid,
            "scan_age_sec": scan_age_sec,
            "auto_approval": bool(cfg.get("enable_conditional_auto_approval", False)),
            "paper_only": True,
        },
        "account": {
            "equity": round(float(status.get("equity", portfolio.get("cash", initial_cap))), 2),
            "cash": round(float(portfolio.get("cash", 0.0)), 2),
            "realized_pnl": realized,
            "closed_trades": len(closed),
            "win_rate_pct": round((wins / len(closed) * 100.0) if closed else 0.0, 1),
            "drawdown_pct": round(float(portfolio.get("current_drawdown", 0.0)) * 100.0, 2),
            "circuit_breaker": str(portfolio.get("circuit_breaker_status", "OK")),
        },
        "basis_yield": {
            "total_carry_usd": round(float(portfolio.get("basis_yield_total_usd", 0.0)), 2),
            "payouts": int(portfolio.get("basis_yield_payouts", 0)),
            "hurdle_apr": float(cfg.get("basis_hurdle_apr", 5.0)),
        },
        "maker_telemetry": summarize_maker_telemetry(),
        "open_positions": open_positions,
        "watchlist": rows,
        "pairs": list(status.get("pairs_desk", []) or []),
        "recent_trades": list(reversed(closed[-15:])),
        "equity_curve": eq_curve[-120:],
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
  svg { width:100%; height:130px; display:block; }
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <div class="title">WEB4 AUTONOMOUS TRADING DESK — LIVE WEB TERMINAL</div>
    <div class="badges" id="top-badges">Loading...</div>
  </div>

  <div class="grid" id="kpi-grid"></div>

  <div class="section">
    <div class="sec-head"><h2>Portfolio Equity Curve (Realized Ledger + Live Mark)</h2><span class="sub" id="eq-range"></span></div>
    <svg id="eq-svg" viewBox="0 0 800 130" preserveAspectRatio="none"></svg>
  </div>

  <div class="section">
    <div class="sec-head"><h2>Open Positions</h2></div>
    <table><thead><tr><th>Symbol</th><th>Strategy</th><th>Side</th><th>Units</th><th>Entry</th><th>Now</th><th>Stop</th><th>Unrealized P&amp;L</th></tr></thead><tbody id="open-body"></tbody></table>
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
    <div class="sec-head"><h2>Recent Closed Fills</h2></div>
    <table><thead><tr><th>Symbol</th><th>Strategy</th><th>Side</th><th>Entry</th><th>Exit</th><th>Net P&amp;L</th><th>Return</th><th>Reason</th></tr></thead><tbody id="closed-body"></tbody></table>
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
function renderCurve(pts){
  const svg = document.getElementById('eq-svg');
  if(!pts || pts.length < 2){ svg.innerHTML = ''; return; }
  const min = Math.min(...pts), max = Math.max(...pts), span = (max - min) || 1;
  document.getElementById('eq-range').textContent = `Min $${fmt(min)} | Max $${fmt(max)} | Now $${fmt(pts[pts.length-1])}`;
  const coords = pts.map((v,i) => `${(i/(pts.length-1)*800).toFixed(1)},${(120 - ((v-min)/span)*105).toFixed(1)}`).join(' ');
  svg.innerHTML = `<polyline fill="none" stroke="#38bdf8" stroke-width="2.5" points="${coords}" />`;
}
function render(d){
  lastData = d;
  document.getElementById('top-badges').innerHTML = `
    <span class="badge ${d.bot.running ? 'ok':'bad'}">${d.bot.running ? 'RUNNING PID '+d.bot.pid : 'STOPPED'}</span>
    <span class="badge">Scan: ${d.bot.scan_age_sec !== null ? d.bot.scan_age_sec+'s ago' : '—'}</span>
    <span class="badge ok">PAPER ONLY</span>
    <span class="badge">${d.bot.auto_approval ? '4-Gate Auto-Approval: ON':'Manual Approval'}</span>`;
  const a = d.account, by = d.basis_yield, mt = d.maker_telemetry;
  document.getElementById('kpi-grid').innerHTML = `
    <div class="card"><div class="k">Total Equity</div><div class="v">$${fmt(a.equity)}</div><div class="sub">Cash: $${fmt(a.cash)}</div></div>
    <div class="card"><div class="k">Drawdown &amp; Breaker</div><div class="v ${a.circuit_breaker==='OK'?'ok':'bad'}">${a.drawdown_pct}%</div><div class="sub">Breaker: ${a.circuit_breaker}</div></div>
    <div class="card"><div class="k">Closed Fills (${a.closed_trades})</div><div class="v ${a.realized_pnl>=0?'ok':'bad'}">$${fmt(a.realized_pnl)}</div><div class="sub">Win Rate: ${a.win_rate_pct}%</div></div>
    <div class="card"><div class="k">Basis Yield Carry</div><div class="v ok">+$${fmt(by.total_carry_usd)}</div><div class="sub">${by.payouts} payouts | Hurdle ${by.hurdle_apr}%</div></div>
    <div class="card"><div class="k">Maker Telemetry</div><div class="v">${mt.fill_rate_pct}%</div><div class="sub">${mt.filled}/${mt.total} fills | ${mt.avg_slippage_bps} bps slip</div></div>`;
  renderCurve(d.equity_curve);
  document.getElementById('open-body').innerHTML = d.open_positions.map(p => `<tr>
    <td><b>${p.symbol}</b></td><td>${p.strategy_id}</td><td>${p.side}</td><td>${p.units}</td>
    <td>$${fmt(p.entry_price)}</td><td>$${fmt(p.current_price)}</td><td>$${fmt(p.trailing_stop)}</td>
    <td class="${p.unrealized_pnl>=0?'ok':'bad'}">$${fmt(p.unrealized_pnl)}</td>
  </tr>`).join('') || '<tr><td colspan="8" class="p-muted">No open positions</td></tr>';
  renderWatchlist();
  document.getElementById('closed-body').innerHTML = d.recent_trades.map(t => `<tr>
    <td><b>${t.symbol}</b></td><td>${t.strategy_id||'—'}</td><td>${t.side}</td>
    <td>$${fmt(t.entry_price,4)}</td><td>$${fmt(t.exit_price,4)}</td>
    <td class="${t.pnl>=0?'ok':'bad'}">$${fmt(t.pnl)}</td><td>${fmt(t.return_pct)}%</td><td>${t.exit_reason||''}</td>
  </tr>`).join('') || '<tr><td colspan="8" class="p-muted">No closed trades yet</td></tr>';
}
async function poll(){ try{ const r = await fetch('/api/state'); if(r.ok) render(await r.json()); }catch(e){} }
document.getElementById('wl-search').addEventListener('input', renderWatchlist);
document.getElementById('wl-filter').addEventListener('change', renderWatchlist);
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
