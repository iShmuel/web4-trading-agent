"""Unit & HTTP integration tests for read-only web_dashboard.py."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from web_dashboard import WebDashboardHandler, build_dashboard_payload


def test_build_dashboard_payload_handles_missing_and_valid_files(tmp_path: Path):
    empty = build_dashboard_payload(
        status_path=tmp_path / "missing_status.json",
        portfolio_path=tmp_path / "missing_port.json",
        config_path=tmp_path / "missing_cfg.json",
        lock_path=tmp_path / "missing.lock",
    )
    assert empty["bot"]["paper_only"] is True
    assert empty["account"]["closed_trades"] == 0
    assert empty["watchlist"] == []

    st = tmp_path / "status.json"
    pf = tmp_path / "port.json"
    cfg = tmp_path / "cfg.json"
    st.write_text(json.dumps({
        "equity": 9500.0,
        "prices": {"PLUG": 1.11},
        "actions": {"PLUG": {"action": "REJECTED", "reason": "Dynamic Router selected 'volatility_squeeze' for PLUG"}},
    }))
    pf.write_text(json.dumps({"cash": 7000.0, "closed_trades": [{"pnl": 50.0, "symbol": "PLUG", "side": "LONG", "entry_price": 1.0, "exit_price": 1.1, "return_pct": 10.0}]}))
    cfg.write_text(json.dumps({"watchlist": ["PLUG"], "blacklisted_symbols": []}))

    pop = build_dashboard_payload(status_path=st, portfolio_path=pf, config_path=cfg, lock_path=tmp_path / "no.lock")
    assert pop["account"]["equity"] == 9500.0
    assert pop["account"]["closed_trades"] == 1
    assert pop["watchlist"][0]["strategy"] == "Vol Squeeze"
    assert len(pop["equity_curve"]) == 3


def test_web_dashboard_http_endpoints():
    server = ThreadingHTTPServer(("127.0.0.1", 0), WebDashboardHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            assert r.status == 200
            assert json.loads(r.read().decode())["status"] == "ok"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=3) as r:
            assert r.status == 200
            data = json.loads(r.read().decode())
            assert "account" in data and "watchlist" in data
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            assert r.status == 200
            assert "WEB4 AUTONOMOUS TRADING DESK" in r.read().decode()
    finally:
        server.shutdown()
        server.server_close()
