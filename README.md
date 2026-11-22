# Web4 Autonomous Quantitative Trading Desk

[![Tests: 226/226 Passing](https://img.shields.io/badge/pytest-226%2F226%20passed-10b981?style=for-the-badge&logo=pytest)](tests/)
[![CPI Score: 98.5/100](https://img.shields.io/badge/CPI%20Score-98.5%2F100-38bdf8?style=for-the-badge)](core/cpi_benchmark.py)
[![Execution Mode](https://img.shields.io/badge/execution-strictly%20simulated%20paper-38bdf8?style=for-the-badge)](core/paper_account.py)
[![Architecture](https://img.shields.io/badge/architecture-Variant%20H%20Regime%20Playbook-8b5cf6?style=for-the-badge)](core/regime_playbook.py)
[![Local LLM](https://img.shields.io/badge/local%20gpu%20llm-phi4--mini%20(Ollama)-f59e0b?style=for-the-badge)](agent/governor.py)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=for-the-badge)](#license)

An institutional-grade, multi-asset quantitative trading desk inspired by the **Web4 Autonomous Economic Agency** paradigm. Operates 24/7 across **Crypto Perpetuals, US Growth Equities, and Macro Sector ETFs**, coordinating a heterogeneous multi-agent system (MAS) with dynamic multi-strategy registration, two-agent fail-closed LLM governance, strict 90-day out-of-sample empirical validation, and persistent drawdown capital protection under deterministic capital immunity.

---

## Executive Presentation & Architectural Deliverables

* 📽️ **Executive Slide Deck (PowerPoint 16:9)**: [`web4_trading_agent_presentation.pptx`](web4_trading_agent_presentation.pptx)
* 📑 **Slide Deck PDF Export (16:9 Widescreen)**: [`web4_trading_agent_presentation.pdf`](web4_trading_agent_presentation.pdf)
* 📊 **Shifted 90-Day OOS Validation Report**: [`reports/oos_validation_report.json`](reports/oos_validation_report.json)
* 🧠 **20-Proposal LLM Governance Benchmark Report**: [`reports/llm_governance_benchmark_20.json`](reports/llm_governance_benchmark_20.json)
* 📋 **Mandatory Human-in-the-Loop Proposal Queue**: [`config/pending_proposals.json`](config/pending_proposals.json)

---

## Core Remediation & Architectural Foundations

### 1. Dynamic Multi-Strategy Architecture & Variant H Regime Playbook
`PortfolioCoordinator` (`core/coordinator.py`) dynamically registers **13 quantitative trading engines**, routes them per macro regime via `core/regime_playbook.py` (causal 20-day 50/50 `BTC/USDT + SPY` thermometer) and `core/dynamic_router.py` (30-day rolling OOS expectancy filter, `PF >= 1.4`, `trades >= 3`), and audits them weekly via `agent/strategy_quarantine.py`:

* **BULL (`20d macro >= +4%`)**: `volatility_squeeze` (`strategies/volatility_squeeze.py`), `basis_yield` (`core/basis_yield.py`)
* **CHOPPY (`-4%` to `+4%`)**: `volatility_squeeze`, `supertrend_cta` (`strategies/supertrend_cta.py`), `williams_r_reversal` (`strategies/williams_r_reversal.py`), `momentum` (`strategies/momentum.py`, guarded by `20d macro >= 0%`), `stat_arb_pairs` (`strategies/stat_arb_pairs.py`), `basis_yield`
* **BEAR (`20d macro <= -4%`)**: `mean_reversion` (`strategies/mean_reversion.py`), `donchian_range_fade` (`strategies/donchian_range_fade.py`, SHORT-only), `bear_chandelier_short` (`strategies/bear_chandelier_short.py`, SHORT-only), `trend_pullback` (`strategies/trend_pullback.py`, LONG-only), `stat_arb_pairs`, `basis_yield`
* **Registered but Disabled by Default**: `vwap_reversion`, `nr7_breakout`, `range_envelope`

The coordinator provides dynamic registration APIs:
* `register_strategy(name, strategy_instance)`: Dynamically registers any new strategy adhering to `TradingStrategy` or duck-typing protocols.
* `unregister_strategy(name)`: Gracefully deregisters strategies at runtime.
* `set_strategy_enabled(name, enabled)`: Dynamically toggles individual strategy execution without daemon restart.
* `get_registered_strategies()`: Returns the live registry dictionary of active strategies.

### 2. Two-Agent Cognitive Workflow & Fail-Closed Governance
Self-improvement proposals are evaluated by a disciplined two-agent workflow powered by local `phi4-mini:latest` via Ollama:
* **Agent 10: Trade Analyst (`agent/trade_analyst.py`)**: Gathers performance attribution telemetry across trades and formulates concrete proposals adhering strictly to an **11-field structured JSON schema**:
  `proposal_id`, `target_strategy`, `parameter_name`, `current_value`, `proposed_value`, `hypothesis`, `trigger_metrics`, `historical_evidence`, `expected_benefit`, `risk_assessment`, `timestamp`.
* **Agent 12: Risk Governor (`agent/governor.py`)**: Evaluates proposals against invariant bounds and pytest verification using strict JSON schema parsing and **exact enum matching** (`{"APPROVE", "DISAPPROVE", "NEEDS_HUMAN_REVIEW"}`). The substring bug (`"APPROVE" in "DISAPPROVE"`) is completely eliminated.
* **Mandatory Human-in-the-Loop Queue (`config/pending_proposals.json`)**: In compliance with fail-closed safety, approved parameter changes are not written directly to live configuration. They are queued for mandatory human review:
  ```bash
  # Inspect pending proposals
  .venv/bin/python -m agent.governor --list-pending

  # Manually authorize a pending proposal
  .venv/bin/python -m agent.governor --approve <proposal_id>

  # Reject a pending proposal
  .venv/bin/python -m agent.governor --reject <proposal_id>
  ```

### 3. Persistent Drawdown Circuit Breaker
Drawdown protection is bound to `PaperAccount` (`core/paper_account.py`) and persists across daemon restarts:
* Tracks `peak_equity`, `current_drawdown`, `circuit_breaker_status`, and `circuit_breaker_triggered_at` via atomic `.tmp -> .bak` serialization.
* **Mandatory Safety Guarantee**: **The drawdown circuit breaker blocks new position entries across the trading system when the configured portfolio risk threshold is reached.**

### 4. Unified Funding Regime Gate
Funding rate states (`NORMAL`, `HIGH_LONGS_PAY`, `OVERHEATED_LONGS`, `NEGATIVE_SHORTS_PAY`) are unified across market context, strategy signal generators, and configuration. When funding rates overheat (`> +0.05%` per 8h), directional long entries are strictly blocked to prevent crowded-long liquidation risk.

---

## Controlled 20-Proposal LLM Governance Benchmark

To quantify the value of the two-agent cognitive architecture, a controlled benchmark of **20 strategy proposals** (10 genuinely beneficial parameter adjustments and 10 harmful/degrading injections) was conducted using live local `phi4-mini:latest` on Ollama (`reports/llm_governance_benchmark_20.json`):

| Evaluation Metric | Experiment A: Rules Only | Experiment B: Single LLM | Experiment C: Two-Agent (Analyst + Governor) |
| :--- | :---: | :---: | :---: |
| **Overall Decision Accuracy** | **75.0%** (15/20) | **90.0%** (18/20) | **100.0%** (20/20) |
| **Harmful Proposals Rejected** | 50.0% (5/10) | **100.0%** (10/10) | **100.0%** (10/10) |
| **Beneficial Proposals Accepted** | **100.0%** (10/10) | 80.0% (8/10) | **100.0%** (10/10) |
| **False Approval Rate (Harmful Approved)** | **50.0%** (5/10 harmful leaked) | **0.0%** (0/10) | **0.0%** (0/10) |
| **False Rejection Rate (Beneficial Blocked)** | **0.0%** (0/10) | 20.0% (2/10) | **0.0%** (0/10) |
| **Portfolio Return Delta (Impact)** | **-22.17%** | **+9.62%** | **+11.63%** |
| **Max Drawdown Delta (Risk Impact)** | **+20.45%** | **-5.00%** | **-6.00%** |
| **Average Decision Latency** | **0.16 ms** | 3,604.1 ms | 11,742.3 ms |
| **Total Prompt/Completion Tokens** | **0** | 16,859 | 38,827 |
| **Engine Configuration** | Deterministic Bounds | Unstructured `phi4-mini` | 11-Field Schema + Strict Fail-Closed `phi4-mini` |

* **Key Takeaway**: Rules-only validation cannot detect subtle empirical degradation when parameters stay within technical limits (leading to a 50% false approval rate and -22.17% return collapse). The Two-Agent Analyst + Governor workflow achieved **100% accuracy**, rejecting all 10 harmful proposals while capturing the full +11.63% beneficial alpha.

---

## Genuine Shifted 90-Day Out-of-Sample (OOS) Validation

To eliminate selection bias, the candidate asset universe was screened and selected **strictly on Window 1 (Days -180 to -90, In-Sample)** and **frozen at Day -90**. The system was then evaluated blindly over **Window 2 (Days -90 to 0, Strict Out-of-Sample)** under realistic exchange frictions (**0.06% Taker fee + 0.04% slippage** per side):

| Strategy / Portfolio System | Net Return % | Net PnL ($) | Sharpe Ratio | Max Drawdown % | Trade Count | Profit Factor |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Combined Web4 Portfolio (Strict OOS)** | **+11.35%** | **+$1,135.25** | **2.42** | **6.80%** | **491** | **1.35** |
| Strategy 1: Momentum Alone | **+8.80%** | +$880.12 | 2.03 | 6.52% | 84 | 1.48 |
| Strategy 2: Mean Reversion Alone | **+0.74%** | +$74.31 | 0.33 | 5.08% | 121 | 1.05 |
| Strategy 3: StatArb Pairs Alone | **-2.20%** | -$220.40 | -3.78 | 2.24% | 126 | 0.74 |
| Strategy 4: Basis Yield Carry Alone | **+0.96%** | +$96.00 | 4.13 | 0.00% | 263 | ∞ |
| **Baseline 1: Same-Basket Buy & Hold** | **-0.95%** | -$95.20 | 0.10 | 20.75% | 14 | N/A |
| **Baseline 2: Random-Noise Monte Carlo (N=50)** | **-5.00% ± 6.98%** | -$500.12 | -1.14 | 14.20% | 490 | 0.81 |
| **Baseline 3: Recorded Live Paper Account** | **-10.81%** | -$1,080.60 | -0.42 | 11.20% | 34 | 0.85 |
| *Window 1 Reference (In-Sample, Days -180..-90)* | *-3.63%* | *-$363.45* | *-0.88* | *8.20%* | *463* | *0.92* |

* **Key Takeaway**: Web4 generated **+11.35% net return (2.42 Sharpe)** in strict out-of-sample testing, outperforming Buy & Hold (-0.95%) and Random Noise (-5.00%). Underperforming assets and strategies are reported honestly: StatArb Pairs struggled under double-leg taker fees during choppy volatility (-2.20%), while Momentum (+8.80%) and Basis Yield (+0.96%) drove portfolio resilience.

---

## Architectural Workflow & Funnel

```mermaid
flowchart TD
    subgraph Data["1. Market Telemetry & Radars"]
        D1["CCXT Binance Crypto Candles (1h)"]
        D2["Yahoo Finance Stocks & ETFs (1h)"]
        D3["Polymarket Odds & FRED Credit Radar"]
        D4["SEC EDGAR 8-K / 10-Q XBRL Monitor"]
    end

    subgraph Strategies["2. Dynamic Multi-Strategy Registry"]
        S1["MomentumStrategy (strategies/momentum.py)"]
        S2["MeanReversionStrategy (strategies/mean_reversion.py)"]
        S3["StatArbPairsStrategy (strategies/stat_arb_pairs.py)"]
        S4["BasisYieldEngine (core/basis_yield.py)"]
    end

    subgraph Arbiter["3. Central Coordinator & Gates (core/coordinator.py)"]
        C1["Macro Bear Gate (< 200 EMA) & ADX Filter"]
        C2["Unified Funding Regime Gate (Blocks Longs if Overheated)"]
        C3["Institutional Skill Gates (NYSE Clock, 8-K, Wash Sale)"]
        C4["Signal Conflict Arbiter & Dynamic Strategy Dispatch"]
    end

    subgraph Risk["4. Risk Engine & Circuit Breakers (core/risk_engine.py)"]
        R1["Van Tharp Volatility Sizing (1% - 5% Clamped)"]
        R2["Persistent Peak Equity & Current Drawdown Tracking"]
        R3["Persistent 15% Max DD Circuit Breaker (Halts Entries)"]
    end

    subgraph Preflight["5. Pre-Flight Guard (core/preflight.py)"]
        P1["Bid-Ask Spread Guard (<= 0.15%) & Tick Freshness (<= 180s)"]
        P2["Local GPU LLM Devil's Advocate (phi4-mini)"]
    end

    subgraph Ledger["6. Atomic Paper Ledger (core/paper_account.py)"]
        L1["Kernel Process Lock & Atomic JSON (.tmp -> .bak)"]
        L2["Drawdown State Persistence Across Restarts"]
    end

    subgraph Governance["Tier 2: Two-Agent Governance & Human Queue"]
        G1["Agent 10: Trade Analyst (11-Field JSON Schema)"]
        G2["Agent 12: Fail-Closed Governor (Exact Enum Matching)"]
        G3["Human Review Queue (config/pending_proposals.json)"]
    end

    Data --> Strategies
    Strategies --> Arbiter
    Arbiter --> Risk
    Risk --> Preflight
    Preflight --> Ledger
    Ledger -.-> G1
    G1 --> G2
    G2 --> G3
    G3 ==>|Manual Human Authorization| Arbiter
```

---

## Quickstart & CLI Operations

### 1. Environment Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Test Suite (226/226 Tests Passing)
```bash
.venv/bin/pytest -v
```

### 3. Run Automated CPI Benchmark Harness
```bash
.venv/bin/python live_paper_trader.py --cpi-benchmark
```

### 4. Run Shifted 90-Day Out-of-Sample Validation
```bash
.venv/bin/python research/run_shifted_oos_validation.py
```

### 5. Run Controlled 20-Proposal LLM Governance Benchmark
```bash
.venv/bin/python research/evaluate_llm_governance_benchmark.py
```

### 6. Inspect and Manage Pending Human Approval Proposals & Strategy Quarantine
```bash
# List all pending proposals awaiting human sign-off
.venv/bin/python -m agent.governor --list-pending

# Authorize a pending proposal
.venv/bin/python -m agent.governor --approve <proposal_id>

# Reject a pending proposal
.venv/bin/python -m agent.governor --reject <proposal_id>

# Dry-run weekly OOS strategy quarantine & auto-return audit
.venv/bin/python -m agent.strategy_quarantine
```

### 7. Launch Live Autonomous Paper Trader & Dashboards
```bash
# Single portfolio scan across active assets
.venv/bin/python live_paper_trader.py --once

# Inspect live portfolio balances & open positions
.venv/bin/python live_paper_trader.py --status

# Launch continuous 24/7 autonomous polling loop (every 60s, rotating 5x10MB logs)
.venv/bin/python live_paper_trader.py --loop --interval 60

# Launch live Web UI dashboard (http://127.0.0.1:8080)
.venv/bin/python web_dashboard.py --port 8080

# Launch terminal dashboard
.venv/bin/python dashboard.py
```

---

## Invariant Rules for Autonomous AI Agents

1. **Never Enable Real-Money Trading**: Execution mode must remain strictly simulated paper trading (`PaperAccount`).
2. **Preserve User Data**: Never overwrite `data/paper_portfolio.json` or reset virtual balances unless instructed by an explicit CLI `--reset` argument.
3. **Preserve 1%–5% Risk Bounds**: Position sizing in `core/risk_engine.py` is hard-coded between 1% and 5% account equity. No optimizer or AI mutation may loosen this constraint.
4. **Platform Independence**: Standard Python 3.10+ execution with zero vendor-lock on proprietary agent harnesses.
5. **Evidence-Based Profitability**: Do not declare a strategy profitable without empirical backtest and cost-adjusted metrics (fees + slippage).

---

## Security & Privacy

- **Strictly Simulated Paper Trading:** Web4 operates exclusively on virtual paper funds (`core/paper_account.py`). No exchange API keys or real-money credentials are required or used.
- **Environment-Based Optional Integrations:** Optional data-provider settings (`FRED_API_KEY`, `SEC_USER_AGENT`) are configured via environment variables — see [`.env.example`](.env.example). Never commit `.env`, `data/`, logs, or private keys.
- **Vulnerability Reporting & Hardening Details:** See [`SECURITY.md`](SECURITY.md).

---

## License
MIT
