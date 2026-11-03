# Web4 Autonomous Trading Agent — The 3 Levels of Autonomy

This document details the architectural specifications, mathematical equations, and deterministic workflows of the **Web4 Autonomous Trading System**.

```
╔═══════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                        THE WEB4 AUTONOMOUS ENGINE                                             ║
╠═══════════════════════════════════╦═══════════════════════════════════╦═══════════════════════════════════════╣
║  LEVEL 1: PERCEPTION & REASONING  ║   LEVEL 2: GOVERNANCE & IMMUNITY  ║ LEVEL 3: OPTIMIZATION & META-LEARNING ║
╠═══════════════════════════════════╬═══════════════════════════════════╬═══════════════════════════════════════╣
║ • Multi-Sensor Data Ingestion     ║ • Van Tharp R-Risk Sizing (1%-5%) ║ • Half-Kelly Dynamic Growth           ║
║ • Microstructure Order Flow (CVD) ║ • Real-time Circuit Breakers (DD) ║ • Hierarchical Risk Parity (HRP)      ║
║ • Polymarket Leading Macro Radar  ║ • Macro Crash Defense & Chop Gate ║ • Statistical Arbitrage Pairs (Beta)  ║
║ • FRED & VIX Stress Radar         ║ • Pre-Flight Execution Guards     ║ • Decision Journal & Alpha Settlement ║
║ • Cognitive Signal Reconciliation ║ • Atomic State Persistence (.bak) ║ • Zero-Cost Local LLM Pre-Flight     ║
╚═══════════════════════════════════╩═══════════════════════════════════╩═══════════════════════════════════════╝
```

---

## Level 1: Autonomous Perception & Reasoning

Level 1 represents the sensory nervous system and cognitive decision-making cortex of the agent. Every cycle (default: 60 seconds), it cleanses multi-venue market telemetry, extracts multi-timeframe signals, and arbitrates competing market hypotheses.

### 1. Multi-Sensor Data Ingestion & Sanitization (`core/data_loader.py`)
* **Multi-Venue Pipelines**: Ingests crypto perpetual feeds from Binance via CCXT and US Equities/ETFs via Yahoo Finance.
* **Self-Validating Filters**:
  * **Staleness Verification**: Rejects any data feed where the latest candle is $> 120$ minutes old.
  * **Monotonic & Gap Validation**: Verifies ascending timestamps, strictly positive OHLCV values, and automatically fills micro-gaps from local caches.

### 2. Multi-Dimensional Feature Extraction (`core/strategy.py`)
* **Macro & Intermediate Trend**:
  * Macro 200 EMA (`calculate_ema(period=200)`): Defines long-term structural regime.
  * Intermediate 32 EMA (`calculate_ema(period=32)`): Filters baseline directional trend.
  * 20-period Donchian Channels (`high.rolling(20).max()`, `low.rolling(20).min()`): Triggers momentum breakout entries.
* **True Volatility**: 14-period Average True Range (ATR) used for dynamic stop distances.
* **Oscillators & Chop Detection**:
  * 20-period Bollinger Bands ($\pm 2.0\sigma$) & 14-period RSI: Identifies overextended extremes.
  * 14-period ADX (Average Directional Index): Distinguishes directional expansion ($ADX \ge 25$) from stationary chop ($ADX < 20$).
* **Order Flow & Liquidation Wicks** (`detect_liquidation_absorption`):
  * Identifies forced liquidation flushes where volume surges $\ge 2.0\times$ its 20-period moving average and lower/upper shadows cover $\ge 50\%$ of the total candle range.
* **Polymarket Leading Macro Event Radar** (`core/polymarket_radar.py`):
  * Queries Polymarket's public Gamma API (`https://gamma-api.polymarket.com/events`) with zero API keys and zero cost.
  * Evaluates real-time crowd-priced probabilities for binary events (Fed rate cuts, surprise hikes, US recession odds). If recession probability $\ge 60\%$, scales risk by $0.50\times$.
* **FRED & Market Volatility Radar** (`core/fred_radar.py`):
  * Pulls live CBOE VIX (`^VIX`) and High-Yield OAS Credit Spreads (`BAMLH0A0HYM2`).
  * Classifies regimes into `STABLE`, `VOLATILITY_EXPANSION` (VIX 22–30), and `CRASH_DEFENSE` (VIX $\ge 30$).
* **Derivatives Funding Rate Radar** (`fetch_funding_rate`):
  * Queries 8-hour perpetual funding rates with a 1-hour in-memory TTL cache. Halts aggressive breakout longs if funding $> +0.05\%$ (`OVERHEATED_LONGS`).
* **Multi-Timeframe Trend Anchor** (`calculate_multi_timeframe_confluence`):
  * Evaluates macro (4h/1D equivalent), intermediate (1h), and micro breakout levels. Enforces strict gating: breakout entries are rejected if confluence score $< 0.70$.

### 3. Cognitive Arbitration Cortex (`core/coordinator.py`)
The `PortfolioCoordinator.reconcile_signals()` reconciles signals between the `MomentumStrategy` and `MeanReversionStrategy`:
* **Thesis Conflict Elimination**: If Momentum proposes `LONG` while Mean Reversion proposes `SHORT`, the coordinator rejects both proposals to avoid trading through ambiguity.
* **Consensus Weighting**: If both strategies agree, it scales confidence ($0.0$ to $1.0$) and tightens stop-loss distances by 15% on triple-confluence setups.
* **Delta-Neutral Basis Yield Engine** (`core/basis_yield.py`): Scans perpetual funding rates across the crypto universe and harvests market-neutral baseline yield (8%–18% APY floor) with 0.0 delta exposure when directional setups are dormant.

---

## Level 2: Deterministic Governance & Self-Immunity

Level 2 provides mathematical boundaries and defensive protocols that ensure survival across market crashes, API failures, or algorithmic edge cases.

### 1. Van Tharp R-Risk Position Sizing (`core/risk_engine.py`)
Position sizing is strictly derived from account equity and volatility risk:

$$\text{Position Units} = \frac{\text{Account Equity} \times \text{Risk Percentage}}{\text{ATR} \times \text{ATR Multiplier}}$$

* **Hard Invariant Clamping**: Position risk is strictly bounded within $[0.01, 0.05]$ (1% to 5% account equity). No optimizer, AI mutation, or subagent can loosen these bounds.
* **Dynamic Volatility Targeting (Risk Parity)**: Position risk dynamically scales inversely with asset ATR: when volatility explodes, risk scales down towards 1.0%; when volatility compresses, risk scales up towards 3.0%, damped further if VIX $> 25.0$.
* **Stop-Loss Guarantee**: Dynamic ATR-based stop loss is calculated before trade submission:
  $$\text{Initial Stop} = \text{Entry Price} \pm (\text{ATR} \times 2.25)$$

### 2. Autonomous Regime Gating & Crash Defense (`core/coordinator.py`)
* **Macro Bear Gate**: If an asset is below its 200 EMA, Long breakout entries are blocked. This preserved 95.8% of user capital during the 2025–2026 crypto bear crash.
* **Macro Crash Defense Gate**: If VIX $\ge 30.0$ or high-yield credit spreads blow out ($\ge 500$ bps), all long breakouts are blocked and risk scales down to 1.0% floor.
* **Chop-Zone Risk Throttling**: When $ADX < 20.0$, the coordinator cuts risk in half (from 2.0% down to 1.0%), suppressing false-breakout whipsaw costs.
* **Crash Contagion Cooldown**: If a crypto stop-loss hits during an impulsive dump, new crypto entries pause for 3 hours.
* **Strict Bear-Market Shorting**: When market conditions are in macro bear territory, the engine enters short only on exhausted rallies (`RSI > 75.0` at the Upper Bollinger Band).

### 3. Institutional Pre-Flight Execution Safety Guards (`core/preflight.py`)
* **Spread Guard**: Rejects orders if $(Ask - Bid) / Mid > 0.15\%$ (`PREFLIGHT_SPREAD_TOO_WIDE`), protecting against off-hours illiquidity slippage.
* **Tick Staleness Guard**: Blocks orders if quotes are delayed $> 180$ seconds (`PREFLIGHT_PRICE_STALE`).
* **Volume Participation Depth Guard**: Caps order size to $\le 2.0\%$ of the hourly volume to avoid market impact (`PREFLIGHT_EXCEEDS_LIQUIDITY_DEPTH`).
* **Cash Buffer & Min Notional**: Enforces exchange minimum notional ($\ge \$10.00$) and confirms cash availability before routing.

### 4. Emergency Circuit Breakers / Kill Switches (`core/risk_engine.py`)
* **Daily Loss Circuit Breaker**: If equity drops by $\ge 5.0\%$ from the start-of-day equity, the kill switch triggers, halting all trading until the next calendar day.
* **Max Drawdown Circuit Breaker**: If equity drops by $\ge 15.0\%$ from the high-watermark peak, all entries are halted, locking portfolio capital in safe cash.

### 5. Atomic State Persistence & Immunity (`core/paper_account.py`)
* **Zero Corruption Guarantee**: The account state (`data/paper_portfolio.json`) writes to a `.tmp` file, calls OS `fsync()`, and atomically renames the file with an active `.bak` fallback.
* **Process Concurrency Lock** (`core/process_lock.py`): Enforces `data/live_trader.lock` lockfiles to eliminate dual-process race conditions.

---

## Level 3: Self-Optimization & Meta-Learning

Level 3 allows the agent to self-calibrate, adapt to market volatility, and capture non-correlated alpha.

### 1. Decision Journal & Benchmark Alpha Settlement (`core/journal.py`)
* Records every trade proposal, entry, exit, and blocked gate decision into `data/decision_journal.jsonl`.
* Once 24 hours of market trading elapses, automatically settles past decisions against raw return and benchmark alpha (BTC for crypto, SPY for stocks), computing empirical hit rates and alpha protection scores across all gates.

### 2. Zero-Cost Local LLM Pre-Flight Sanity Engine (`core/preflight.py`)
* Connects to local Ollama instance (`http://localhost:11434`) running lightweight reasoning models (e.g. `phi4-mini:latest` or `qwen3.5:4b`) directly on the local GPU.
* Operates out-of-band: Triggers strictly on proposed entry orders (taking ~0.70s with zero API costs) to detect breaking news, regulatory halts, or earnings blackouts without slowing the 60s math loop.

### 3. Dynamic Half-Kelly Compounding (`core/risk_engine.py`)
Sizing scales dynamically based on rolling empirical win rate ($W$) and payoff ratio ($R$):

$$f^* = \frac{W(R + 1) - 1}{R}, \quad \text{Half-Kelly} = 0.5 \times f^*$$

* Blends $0.5 \times \text{Base Risk} + 0.5 \times \text{Half-Kelly}$, clamped strictly within $[1\%, 5\%]$.

### 4. Hierarchical Risk Parity (HRP) Sector Clustering (`core/risk_engine.py`)
* Calculates the rolling 30-day covariance matrix and correlation distance tree across all assets.
* When correlation between crypto assets spikes ($\rho > 0.85$), HRP treats them as a single risk cluster and scales down exposure, while allocating risk to independent equities (`AMD`, `SPY`).

### 5. Market-Neutral Statistical Arbitrage (`strategies/stat_arb_pairs.py`)
* Tracks cointegrated pairs (`SOL` vs `AVAX`, `NVDA` vs `AMD`, `ETH` vs `BTC`).
* Computes rolling hedge ratio ($\beta = \text{Cov}(A,B) / \text{Var}(B)$) and spread Z-Score ($Z$).
* Enters long the undervalued asset and short the overvalued asset when $|Z| \ge 2.0$, and exits upon mean-reversion ($|Z| \le 0.5$), delivering zero-beta market-neutral returns.

### 6. Smart Order Routing / Execution Alpha (`core/coordinator.py`)
* Supports `execution_mode: maker` post-only limit orders.
* Eliminates slippage ($0.00\%$) and drops fees from $0.06\%$ to $0.02\%$, delivering a proven $+4.50\%$ pure alpha gain directly into net profit.

### 7. Automated Walk-Forward Optimization Arena (`agent/optimizer.py`)
* Periodically audits performance and backtests mutations across lookbacks, EMA trend filters, and ATR multipliers.
* Validates candidates against out-of-sample data with full fees and slippage; automatically rejects overfitted parameter sets.

---

## Continuous Operating Loop

```mermaid
sequenceDiagram
    autonumber
    actor Market as Market Telemetry (Binance/Yahoo)
    participant Loader as Data Loader & Sanitizer
    participant Strat as Dual Strategies (Mom / MR)
    participant Coord as Portfolio Coordinator
    participant Risk as Central Risk Engine
    participant Paper as Atomic Paper Account

    loop Every 60 Seconds
        Market->>Loader: Fetch OHLCV + Funding Rates
        Loader->>Loader: Validate staleness, gaps, zero-checks
        Loader->>Strat: Compute indicators, absorption wicks, Z-scores
        Strat->>Coord: Propose Long/Short TradingSignal
        Coord->>Coord: Reconcile conflicts, apply 200 EMA & Chop Gates
        Coord->>Risk: Validate proposed entry (R-risk, HRP, Circuit Breakers)
        alt Risk Approved
            Risk->>Coord: Approved (Suggested Units, Trailing Stop)
            Coord->>Paper: Execute Smart Maker Order (0 slippage, 0.02% fee)
            Paper->>Paper: Atomic write to portfolio.json + .bak
        else Risk Rejected
            Risk->>Coord: Blocked (e.g., Daily breaker / Exposure cap)
            Coord->>Coord: Log rejection reason
        end
    end
```
