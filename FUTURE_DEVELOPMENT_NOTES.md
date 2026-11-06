# Future Development Notes (Post-Review Roadmap)

This document records architectural, analytical, and operational items identified during the academic and institutional review of the **Web4 Autonomous Trading Desk** that are **not currently implemented** in the active codebase, but are prioritized for future research and development cycles.

---

### 1. Automated Central Performance Index (CPI) Benchmark Harness

* **Status**: Planned / Academic Benchmark
* **Suggested Priority**: Medium (P2)
* **What is Missing:**
  The Central Performance Index (CPI) is formalized in the presentation as a **conceptual and mathematical evaluation framework** for comparing our multi-agent architecture against two theoretical baselines:
  1. *Baseline 1: Conventional Single-Agent System* (monolithic, single-strategy, single-asset bot).
  2. *Baseline 2: Centralized Multi-Strategy Agent* (monolithic multi-asset script without independent risk governance or conflict arbitration).
  An automated simulation script executing these theoretical baselines side-by-side with our system does not currently exist in `research/`.
* **Why it Matters:**
  While historical backtests prove our system's standalone performance (+66.4% net return, Sharpe 2.03, Max DD 13.4%), having an empirical comparative execution harness would provide quantitative benchmark proof of multi-agent superiority over monolithic architectures under identical market candles and cost models.
* **Expected Benefit:**
  Empirically validates the thesis that decoupled agent arbitration reduces maximum drawdown by over 50% compared to monolithic single-strategy or uncoordinated multi-strategy systems.
* **Implementation Plan:**
  Develop `research/benchmark_cpi.py` that implements mock baseline harnesses, simulates all three architectural topologies over the identical 180-day 21-asset dataset, and outputs an automated CPI comparative scorecard.

---

### 2. Dynamic Kill-Switch Half-Step Recovery Protocol (Probationary Reentry)

* **Status**: Planned / Architectural Refinement
* **Suggested Priority**: High (P1)
* **What is Missing:**
  In `core/risk_engine.py`, when portfolio drawdown reaches the 15% maximum limit, the circuit breaker triggers a hard latch (`self._is_halted = True`). In the current implementation, this halt is permanent until an engineer manually clears it via `reset_kill_switch()`.
* **Why it Matters:**
  In automated production operations, locking the portfolio in 100% cash defense guarantees capital preservation (protecting 85% of equity), but leaves the system unable to autonomously re-enter the market when a multi-month bear market transitions back into a bull expansion wave without manual operator intervention.
* **Expected Benefit:**
  Allows continuous autonomous operations without human intervention while preserving strict capital safety guarantees.
* **Implementation Plan:**
  Implement an automated, staged **Probationary Reentry Protocol**:
  1. Mandatory 72-hour macro cooldown following a kill switch event.
  2. Reentry condition: FRED VIX must return to `STABLE` ($<20$) and 200 EMA Macro Gate must confirm structural reclaim.
  3. Risk throttle: Capital risk is clamped to an ultra-conservative $0.5\%$ micro-pilot allocation.
  4. Gradual scaling: Normal $2.0\%$ risk sizing is only restored after 3 consecutive winning trades.

---

### 3. Maker Order Queue Tracking & Adaptive Re-Quoting Engine

* **Status**: Future Research / Execution Modeling
* **Suggested Priority**: High (P1)
* **What is Missing:**
  The current maker model in `core/paper_account.py` applies a passive $0.02\%$ maker fee and $0.00\%$ slippage under next-open execution. However, it assumes passive limit orders are filled at the opening quote without modeling real-world order book queue depth, partial fills, or adverse selection during violent market gaps.
* **Why it Matters:**
  In real-world limit order execution, when price gaps violently upward during an impulsive breakout candle, a passive bid posted at the previous close or mid-spread may be left unfilled as the market runs away, leading to missed trades on high-velocity runners.
* **Expected Benefit:**
  Eliminates execution slippage estimation errors and provides 100% realistic fill probabilities for high-frequency or high-volatility breakout setups.
* **Implementation Plan:**
  Develop an asynchronous limit order queue simulator:
  1. Ingest L2 order book depth (bid/ask volume levels) via CCXT WebSocket.
  2. Implement an adaptive re-quoting loop that cancels and replaces unfilled limit orders closer to the inside spread if unfilled after 30 seconds.
  3. Model partial fills and fill-or-kill (FOK) constraints.

---

### 4. Decentralized Multi-Process Agent Distribution (Microservices / RPC)

* **Status**: Future Research / Infrastructure
* **Suggested Priority**: Low (P3)
* **What is Missing:**
  All 8 agents currently execute as modular Python classes within a single sequential process loop (`live_paper_trader.py`). They run within one operating system thread on the local host.
* **Why it Matters:**
  If an external REST call in Agent 1 (e.g. Binance API latency spike) or a heavy GPU tensor generation in Agent 8 (local LLM inference) experiences a temporary delay, the 60-second cycle can be extended. In an institutional production environment, decoupling agents into independent services ensures zero cross-thread blocking.
* **Expected Benefit:**
  Guarantees strict sub-second loop execution determinism and allows agents to be horizontally scaled across separate physical machines or cloud instances.
* **Implementation Plan:**
  Refactor the 8 agents into decoupled microservices:
  1. Run each agent as an independent OS service / Docker container.
  2. Inter-agent communication via high-throughput ZeroMQ, Redis Pub/Sub, or gRPC message queues.
  3. Asynchronous heartbeat monitoring with automatic service restart capabilities.

---

### 5. Multi-Broker Live Execution Gateways (Alpaca / Interactive Brokers)

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
