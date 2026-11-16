# Future Development Notes (Unbuilt / Queued Roadmap)

This document tracks **only unbuilt and queued items** for the **Web4 Autonomous Trading Desk**. *(Already implemented and deployed features — Option B Dynamic Rolling Router, CPI Benchmark Harness, 72h Probationary Reentry, Adaptive Maker Re-Quoting & Telemetry, Async Agent Runner, and Basis Yield 5.0% Hurdle — are documented in [`PROJECT_CURRENT_STATE_AUDIT.md`](PROJECT_CURRENT_STATE_AUDIT.md).)*

---

### 1. Multi-Broker Live Execution Gateways (Alpaca / Interactive Brokers)

* **Status**: Planned / Institutional Deployment
* **Suggested Priority**: Medium (P2 - Dependent on Live Capital Authorization)
* **What is Missing:**
  Live real-money order routing is permanently disabled by Invariant Rule #1 (`PaperAccount` simulation only). The repository does not currently contain authenticated API connectors for real brokerages (Alpaca, Interactive Brokers, Binance API keys).
* **Why it Matters:**
  Transitioning from simulated forward paper trading to live institutional capital deployment requires audited, authenticated order gateways that enforce API key rotation, multi-signature approvals, and hardware security enclave integration.
* **Expected Benefit:**
  Seamless execution transition from paper verification to real capital deployment across traditional equities (Interactive Brokers / Alpaca) and digital assets (Binance / Kraken).
* **Implementation Plan:**
  Develop audited broker adapter interfaces (`core/broker_alpaca.py`, `core/broker_ibkr.py`):
  1. Enforce strict environment-variable credential isolation (zero hardcoded secrets).
  2. Pre-trade multi-signature approval cards with human-in-the-loop authorization toggles.
  3. Live margin and collateral liquidation buffers.

---

### 2. Forensic Audit Sample Window Calibration (Operator Reminder: Review in 1–2 Weeks)

* **Status**: Queued for Operator Review (Target: Mid/Late November 2026)
* **Suggested Priority**: Medium (P2)
* **What is Slated for Review:**
  Currently, `forensic_trade_interval` is kept at `15` trades in `config/active_config.json`. We evaluated increasing this parameter to `25` or `30` trades to align with the Central Limit Theorem ($N \ge 30$) and provide a richer statistical sample of losing trades (5–6 losses vs. 2–3 losses) for `Agent 10 (TradeAnalyst)` and local LLM root-cause diagnosis.
* **Why it Matters:**
  Running forensic audits too frequently ($N < 15$) can be susceptible to short-term market noise, potentially triggering premature parameter adjustments. Expanding to $N = 25$ provides robust statistical confidence while maintaining responsive agility.
* **Action Item for Operator:**
  After accumulating 1–2 weeks of forward live paper trading history (50+ closed trades across active regimes), review `data/trade_forensic_report.json` and decide whether to graduate `forensic_trade_interval` from `15` to `25`.

---

### 3. Zero-Dependency Single-File Web Dashboard (`web_dashboard.py`)

* **Status**: Planned / Operator Interface
* **Suggested Priority**: Medium (P2)
* **What is Missing:**
  The current monitoring dashboard (`dashboard.py`) is a terminal-based ANSI curses interface. While ultra-fast, lightweight, and zero-dependency, it requires terminal access (SSH or local console) and cannot be viewed directly from a smartphone or web browser on a local network.
* **Why it Matters:**
  When deploying the bot to a cloud VPS or remote headless server, having an optional browser-based UI enables remote monitoring without keeping an active SSH session or tmux window attached.
* **Expected Benefit:**
  Allows real-time monitoring of portfolio equity, open positions, cointegrated pairs Z-scores, and live execution logs from any web browser or mobile phone on the local network/VPN with zero cloud costs.
* **Implementation Plan:**
  Develop a single-file, zero-external-dependency web server (`web_dashboard.py`):
  1. Use standard Python `http.server` or lightweight ASGI/FastAPI.
  2. Implement an embedded modern HTML/CSS/JS single-page UI (dark theme, responsive mobile layout).
  3. Stream live updates via Server-Sent Events (SSE) or a 2-second polling REST endpoint reading `data/live_status.json` and `data/paper_portfolio.json`.
  4. Ensure 100% read-only operations using non-blocking read locks so it never interferes with the live trading daemon.

---

### 4. Portable Cloud / VPS Deployment Bootstrap (`scripts/setup_vps.sh`) & Configurable Ollama Endpoint

* **Status**: Planned / Infrastructure Portability
* **Suggested Priority**: Medium (P2)
* **What is Missing:**
  Local LLM model weights (`phi4-mini:latest`, ~2.5 GB) cannot be committed to Git (exceeds GitHub's 100 MB file limit), and [`agent/llm_client.py`](agent/llm_client.py) currently defaults to `http://localhost:11434/api/generate`. While the system gracefully falls back to deterministic logic without Ollama, migrating to a fresh cloud server or VPS requires manual setup if narrative LLM reasoning is desired.
* **Why it Matters:**
  Ensures 1-command reproducibility when migrating the trading desk to an EU cloud VPS (2–4 vCPU, 4 GB RAM, 10–20 GB SSD) or connecting a lightweight VPS to a remote/home GPU Ollama instance.
* **Implementation Plan:**
  1. Add `scripts/setup_vps.sh` to automate `.venv` creation, `pip install -r requirements.txt`, `systemd` service template generation for `live_paper_trader.py --loop --interval 60`, and an optional `--with-llm` flag that installs Ollama and runs `ollama pull phi4-mini:latest`.
  2. Update [`agent/llm_client.py`](agent/llm_client.py) to read `OLLAMA_URL` and `OLLAMA_MODEL` from environment variables (`.env`), allowing a small 4 GB VPS to either run deterministic-only or point to an external Ollama host.

---

### 5. Multi-Strategy Alpha Expansion Roadmap (12 Candidate Strategies)

* **Status**: Planned / Research Queue (Detailed guide in [`docs/NEW_STRATEGIES_ROADMAP.md`](docs/NEW_STRATEGIES_ROADMAP.md))
* **Suggested Priority**: Medium (P2)
* **What is Planned:**
  Expand beyond the current 4 active strategies (`momentum`, `mean_reversion`, `stat_arb_pairs`, `basis_yield`) with **12 non-overlapping candidate strategies** built strictly on existing OHLCV, volume, funding-rate, and FRED macro feeds (zero new paid APIs):
  * **Volatility & Breakout:** Volatility Squeeze Breakout (*Priority #1*), US Session Open Drive, Volatility-Targeted Trend
  * **Trend & Pullback:** Trend Pullback (*Priority #2*), Dual-Timeframe Confluence Trend
  * **Mean Reversion & Microstructure:** VWAP Fair-Value Reversion, RSI Bull/Bear Divergence, Weekend Crypto / Opening Gap Fade
  * **Cross-Asset & Derivatives:** Funding-Rate Contrarian Squeeze (`SHORT_SQUEEZE_PRONE`), BTC-Lead / Altcoin Lag Spillover, Cross-Sectional Relative Strength Rotation, Macro Risk-Off Defensive Hedge
* **Validation Gate:**
  No candidate strategy will be enabled in `config/active_config.json` without walk-forward OOS backtesting (fees + slippage), clearing `PF >= 1.15` in the Option B Dynamic Rolling Router, CPI benchmark validation (`core/cpi_benchmark.py`), and Governor approval.
