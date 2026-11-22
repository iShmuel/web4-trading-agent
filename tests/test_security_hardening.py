"""Security & public-repository hygiene regression tests."""

import pathlib
import re
import subprocess
from http import HTTPStatus
from unittest.mock import MagicMock

from agent.governor import _portable_config_path, _resolve_target_config_path, PROJECT_ROOT
from web_dashboard import WebDashboardHandler


def test_governor_portable_config_path_avoids_absolute_local_leaks():
    """target_config_path inside the repo must serialize as a portable relative path."""
    cfg_abs = PROJECT_ROOT / "config" / "active_config.json"
    rel = _portable_config_path(cfg_abs)
    assert rel == "config/active_config.json"
    assert not rel.startswith("/")
    assert _resolve_target_config_path(rel).resolve() == cfg_abs.resolve()


def test_web_dashboard_emits_defensive_security_headers():
    """WebDashboardHandler._send must attach standard defensive HTTP security headers."""
    handler = WebDashboardHandler.__new__(WebDashboardHandler)
    headers = {}
    handler.send_response = MagicMock()
    handler.send_header = MagicMock(side_effect=lambda k, v: headers.__setitem__(k, v))
    handler.end_headers = MagicMock()
    handler.wfile = MagicMock()

    handler._send(HTTPStatus.OK, "application/json", b'{"status":"ok"}')

    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert headers.get("Referrer-Policy") == "no-referrer"
    assert "default-src 'self'" in headers.get("Content-Security-Policy", "")
    assert headers.get("Cache-Control") == "no-store"


def test_tracked_files_contain_no_local_paths_or_high_confidence_secrets():
    """Automated guardrail preventing accidental commits of local user paths or secrets."""
    tracked = subprocess.check_output(["git", "ls-files"], cwd=str(PROJECT_ROOT), text=True).splitlines()
    forbidden_local = re.compile(r"/run/media/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+")
    secret_rx = re.compile(
        r"(AKIA[0-9A-Z]{16}|(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
    )
    skip_suffixes = {".png", ".jpg", ".jpeg", ".pdf", ".pptx", ".ico"}
    offenders = []
    for rel in tracked:
        if rel == "tests/test_security_hardening.py":
            continue
        p = PROJECT_ROOT / rel
        if not p.exists() or p.suffix.lower() in skip_suffixes:
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        if forbidden_local.search(txt):
            offenders.append((rel, "local_path"))
        if secret_rx.search(txt):
            offenders.append((rel, "secret_pattern"))
    assert offenders == [], f"Security/hygiene violations found in tracked files: {offenders}"
