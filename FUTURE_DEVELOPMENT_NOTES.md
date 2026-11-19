# Future Development Notes (Unbuilt / Queued Roadmap)

This document tracks **only unbuilt and queued items** for the **Web4 Autonomous Trading Desk**. *(Already implemented and deployed features — Web4 Dynamic Rolling Router, CPI Benchmark Harness, 72h Probationary Reentry, Adaptive Maker Re-Quoting & Telemetry, Async Agent Runner, Basis Yield 5.0% Hurdle, Volatility Squeeze, Trend Pullback, VWAP Fair-Value Reversion, Idle-Cash Dynamic Sweep, Portfolio Correlation Guard, Bear-Gated Shorts, and Selective 3% Leader Risk — are documented in [`PROJECT_CURRENT_STATE_AUDIT.md`](PROJECT_CURRENT_STATE_AUDIT.md).)*

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

* **Status**: Queued for Operator Review (Target: Late November 2026)
* **Suggested Priority**: Medium (P2)
* **What is Slated for Review:**
  Currently, `forensic_trade_interval` is kept at `15` trades in `config/active_config.json`. We evaluated increasing this parameter to `25` or `30` trades to align with the Central Limit Theorem ($N \ge 30$) and provide a richer statistical sample of losing trades (5–6 losses vs. 2–3 losses) for `Agent 10 (TradeAnalyst)` and local LLM root-cause diagnosis.
* **Action Item for Operator:**
  After accumulating 1–2 weeks of forward live paper trading history (50+ closed trades across active regimes), review `data/trade_forensic_report.json` and decide whether to graduate `forensic_trade_interval` from `15` to `25`.

---

### 3. Portable Cloud / VPS Deployment Bootstrap (`scripts/setup_vps.sh`) & Configurable Ollama Endpoint

* **Status**: Planned / Infrastructure Portability
* **Suggested Priority**: Medium (P2)
* **What is Missing:**
  Local LLM model weights (`phi4-mini:latest`, ~2.5 GB) cannot be committed to Git, and [`agent/llm_client.py`](agent/llm_client.py) defaults to `http://localhost:11434/api/generate`.
* **Implementation Plan:**
  1. Add `scripts/setup_vps.sh` to automate `.venv`, `requirements.txt`, `systemd` service template, and optional `--with-llm` Ollama setup.
  2. Update [`agent/llm_client.py`](agent/llm_client.py) to read `OLLAMA_URL` and `OLLAMA_MODEL` from `.env`.

---

### 4. Remaining Multi-Strategy Alpha Expansion Candidates (9 Unbuilt Strategies)

* **Status**: Planned / Research Queue (Detailed guide in [`docs/NEW_STRATEGIES_ROADMAP.md`](docs/NEW_STRATEGIES_ROADMAP.md))
* **Suggested Priority**: Medium (P2)
* **What is Planned:**
  *(Note: Volatility Squeeze, Trend Pullback, and VWAP Reversion are now **IMPLEMENTED**; BTC Lead-Lag was **REJECTED** after empirical 90D/180D backtest showed 0% gain.)*
  Remaining unbuilt candidates: Funding-Rate Contrarian Squeeze (`SHORT_SQUEEZE_PRONE`), Cross-Sectional Relative Strength Rotation, US Session Open Drive, Macro Risk-Off Defensive Hedge, RSI Bull/Bear Divergence, Weekend Crypto / Opening Gap Fade, Dual-Timeframe Confluence Trend, and Volatility-Targeted Trend.

---

### 5. Live-vs-Backtest Execution Drift Monitor (`core/journal.py`)

* **Status**: Planned / Execution Quality & Decay Detection
* **Suggested Priority**: Medium (P2)
* **What is Missing:**
  Automated weekly comparison between actual live fills (`data/paper_portfolio.json` + `data/maker_telemetry.jsonl`) and backtest expectations (win rate, PF, slippage bps).

---

### 6. Automated Data Retention, Log Rotation & Parquet/SQLite Storage

* **Status**: Planned / Long-Running VPS Hygiene
* **Suggested Priority**: Low-Medium (P3)
* **What is Missing:**
  Scheduled cleanup/archival of expired `data/*.csv` caches, `RotatingFileHandler` for `live_trader.log`, and optional SQLite/Parquet storage for `decision_journal.jsonl` and `maker_telemetry.jsonl`.

---

### 7. Realized Tax-Lot & Fee-Tier Savings Ledger Export

* **Status**: Planned / Accounting & Broker Readiness
* **Suggested Priority**: Low (P3)
* **What is Missing:**
  FIFO / specific-lot realized gain/loss, holding-period, wash-sale, and cumulative maker-vs-taker fee savings CSV/JSON export from `closed_trades` and `maker_telemetry.jsonl`.

---

### 8. Automatic Strategy-Level OOS Quarantine & Rehabilitation Gate (Agent 11 / Agent 12)

* **Status**: Planned / Autonomous Portfolio Defense
* **Suggested Priority**: High (P1)
* **What is Missing:**
  Currently, Agent 9 prunes/rehabilitates individual *assets*, while Agent 10 and Agent 11 tune *parameters inside* each strategy — but no agent automatically evaluates all 9 strategies in a **zero-lookahead rolling walk-forward Out-of-Sample (OOS) portfolio tournament** with authority to toggle `enable_<strategy>` flags on or off.
* **Why it Matters:**
  In our November 2026 strict zero-lookahead OOS audit (75.5 unseen days, full `0.06%` fee + `0.04%` slippage), keeping all 9 strategies enabled under a 14-day router lost `-36.27%` (primarily due to `vwap_reversion` `-$2,151.79`, `momentum` `-$679.29`, and `nr7_breakout` `-$585.02`), whereas quarantining OOS-negative engines and running only the 4 OOS-proven engines (`mean_reversion`, `range_envelope`, `basis_yield`, and equity `stat_arb_pairs`) with a 30-day router (`min_trades >= 3`, `min_pf >= 1.4`) turned the exact same OOS period into **`+24.12%` (`$12,412` in 63.8d / `$13,566` 90-day pace, `4.53%` MaxDD)**.
* **Implementation Plan:**
  1. Add a weekly non-blocking OOS strategy-level tournament in `agent/multi_strategy_optimizer.py` that replays each enabled and disabled strategy strictly out-of-sample across the active watchlist.
  2. Submit a structured governance proposal to Agent 12 (`agent/governor.py`) to set `enable_<strategy>: false` for any strategy with negative rolling OOS Net PnL or OOS Profit Factor `< 1.15`, and automatically rehabilitate (`enable_<strategy>: true`) when its rolling OOS turns profitable (`OOS Net PnL > 0`, `OOS PF >= 1.30`, `MaxDD <= 10%`) for two consecutive checks.
