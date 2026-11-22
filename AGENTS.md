# Engineering Guidelines for Autonomous AI Agents

This repository is designed to be maintained, inspected, and improved by autonomous coding agents and human engineers alike.

## Invariant Rules for AI Agents
1. **Never Enable Real-Money Trading**: Execution mode must remain strictly simulated paper trading (`PaperAccount`).
2. **Preserve User Data**: Never overwrite `data/paper_portfolio.json` or reset virtual balances unless instructed by an explicit CLI `--reset` argument.
3. **Preserve 1%-5% Risk Bounds**: Position sizing in `core/risk_engine.py` is hard-coded between 1% and 5% account equity. No optimizer or AI mutation may loosen this constraint.
4. **Platform Independence**: Do not add dependencies on Antigravity CLI, Gemini CLI, or proprietary agent harnesses. Code must execute using standard Python 3.10+.
5. **Evidence-Based Profitability**: Do not declare a strategy profitable without empirical backtest and cost-adjusted metrics (fees + slippage). Report underperforming assets honestly.

## Public Repository Security & Hygiene Rules
Because this repository is public on GitHub, every update, commit, doc edit, and generated artifact **must** obey these rules:
6. **Zero Secrets or Credentials**: Never hardcode or commit API keys, tokens, passwords, private keys, webhooks, or exchange credentials. All optional integrations (`FRED_API_KEY`, `SEC_USER_AGENT`) must be read exclusively from environment variables and documented only with placeholders in `.env.example`.
7. **Zero Personal Info or Local Paths**: Never commit personal names, personal email addresses, OS usernames, drive labels, or absolute local filesystem paths in code, configs, proposals, reports, or Markdown links. Always use repo-relative paths (`Path(__file__).resolve()...`, relative Markdown links, and `"human_quant_operator"` for operator metadata).
8. **Never Commit Runtime State or Caches**: Never stage or commit `data/`, `.env`, `.env.*` (except `.env.example`), `*.log`, `*.csv`, `*.db`, `*.bak`, `*.lock`, `*.tmp`, or `config/last_deployment_snapshot.json`.
9. **Pre-Push Security Verification**: Before every `git push`, run `pytest tests/test_security_hardening.py` (and the full `pytest` suite) plus inspect `git diff --cached` to confirm zero leaked paths, PII, or secrets.
10. **Preserve Web & Network Hardening**: Keep `web_dashboard.py` read-only, bound to `127.0.0.1` by default, and serving defensive HTTP security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy`).
