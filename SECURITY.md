# Security Policy — Web4 Autonomous Quantitative Trading Desk

## Simulated Paper-Trading Scope
Web4 is strictly a **simulated paper-trading and quantitative research platform** (`core/paper_account.py`).
- **No real-money execution:** Live brokerage or exchange order-routing is intentionally absent.
- **No exchange credentials required:** Public OHLCV candles and funding rates are fetched from public read-only endpoints (CCXT Binance public market data and Yahoo Finance). Do **not** supply private exchange API keys, withdrawal keys, or wallet seed phrases.

## Managing Optional Credentials & Configuration Safely
All optional external-data settings are read exclusively from environment variables — never hardcoded in source code or committed JSON configs:
- `FRED_API_KEY` *(optional)*: Enables live St. Louis Fed credit-spread queries in `core/fred_radar.py`.
- `SEC_USER_AGENT` *(optional)*: Identifies your client to SEC EDGAR per SEC fair-access rules in `core/skill_intelligence_gate.py`.

See [`.env.example`](.env.example) for the complete template. Local overrides should live in `.env` or `config/*.local.json`, both of which are excluded by [`.gitignore`](.gitignore).

## Local Dashboard Network Exposure
`web_dashboard.py` is a **read-only** monitoring UI that binds to `127.0.0.1:8080` (localhost only) by default and serves defensive HTTP headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and `Content-Security-Policy`). Avoid exposing it directly to untrusted public networks without a reverse proxy and authentication.

## Never Commit These Files
The following must never be committed to Git (enforced via `.gitignore`):
- `.env`, `.env.*` (except `.env.example`), `.secrets/`, `*.pem`, `*.key`, `*credentials*.json`, `*secret*.json`
- Runtime portfolio state, trade logs, and cached market CSVs (`data/`, `*.log`, `*.csv`, `*.db`)
- Machine-specific paths or personal operator names in proposals, configs, or documentation

## Reporting a Vulnerability
If you discover a security vulnerability, credential exposure, or safety-invariant bypass:
1. **Do not open a public issue** containing exploit details or sensitive data.
2. Report privately via GitHub Security Advisories (`Security → Advisories → Report a vulnerability`) on the repository.
3. Include reproduction steps, affected file paths, and potential impact. We aim to acknowledge reports within 72 hours.
