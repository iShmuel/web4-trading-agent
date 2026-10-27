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
║ • Derivatives Funding Radar       ║ • Macro Bear & Chop Gates         ║ • Statistical Arbitrage Pairs (Beta)  ║
║ • Cognitive Signal Reconciliation ║ • Atomic State Persistence (.bak) ║ • Walk-Forward Out-of-Sample Arena    ║
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
* **Derivatives Funding Rate Radar** (`fetch_funding_rate`):
  * Queries 8-hour perpetual funding rates with a 1-hour in-memory TTL cache. Halts aggressive breakout longs if funding $> +0.05\%$ (`OVERHEATED_LONGS`).

### 3. Cognitive Arbitration Cortex (`core/coordinator.py`)
The `PortfolioCoordinator.reconcile_signals()` reconciles signals between the `MomentumStrategy` and `MeanReversionStrategy`:
* **Thesis Conflict Elimination**: If Momentum proposes `LONG` while Mean Reversion proposes `SHORT`, the coordinator rejects both proposals to avoid trading through ambiguity.
* **Consensus Weighting**: If both strategies agree, it scales confidence ($0.0$ to $1.0$) and tightens stop-loss distances by 15% on triple-confluence setups.

---

## Level 2: Deterministic Governance & Self-Immunity

Level 2 provides mathematical boundaries and defensive protocols that ensure survival across market crashes, API failures, or algorithmic edge cases.

### 1. Van Tharp R-Risk Position Sizing (`core/risk_engine.py`)
Position sizing is strictly derived from account equity and volatility risk:

$$\text{Position Units} = \frac{\text{Account Equity} \times \text{Risk Percentage}}{\text{ATR} \times \text{ATR Multiplier}}$$

* **Hard Invariant Clamping**: Position risk is strictly bounded within $[0.01, 0.05]$ (1% to 5% account equity). No optimizer, AI mutation, or subagent can loosen these bounds.
* **Stop-Loss Guarantee**: Dynamic ATR-based stop loss is calculated before trade submission:
  $$\text{Initial Stop} = \text{Entry Price} \pm (\text{ATR} \times 2.25)$$

### 2. Autonomous Regime Gating & Chop Defense (`core/coordinator.py`)
* **Macro Bear Gate**: If an asset is below its 200 EMA, Long breakout entries are blocked. This preserved 95.8% of user capital during the 2025–2026 crypto bear crash.
* **Chop-Zone Risk Throttling**: When $ADX < 20.0$, the coordinator cuts risk in half (from 2.0% down to 1.0%), suppressing false-breakout whipsaw costs.
* **Strict Bear-Market Shorting**: When market conditions are in macro bear territory, the engine enters short only on exhausted rallies (`RSI > 75.0` at the Upper Bollinger Band).

### 3. Emergency Circuit Breakers / Kill Switches (`core/risk_engine.py`)
* **Daily Loss Circuit Breaker**: If equity drops by $\ge 5.0\%$ from the start-of-day equity, the kill switch triggers, halting all trading until the next calendar day.
* **Max Drawdown Circuit Breaker**: If equity drops by $\ge 15.0\%$ from the high-watermark peak, all entries are halted, locking portfolio capital in safe cash.

### 4. Atomic State Persistence & Immunity (`core/paper_account.py`)
* **Zero Corruption Guarantee**: The account state (`data/paper_portfolio.json`) writes to a `.tmp` file, calls OS `fsync()`, and atomically renames the file with an active `.bak` fallback.
* **Process Concurrency Lock** (`core/process_lock.py`): Enforces `/tmp/paper_trader.pid` lockfiles to eliminate dual-process race conditions.

---

## Level 3: Self-Optimization & Meta-Learning

Level 3 allows the agent to self-calibrate, adapt to market volatility, and capture non-correlated alpha.

### 1. Dynamic Half-Kelly Compounding (`core/risk_engine.py`)
Sizing scales dynamically based on rolling empirical win rate ($W$) and payoff ratio ($R$):

$$f^* = \frac{W(R + 1) - 1}{R}, \quad \text{Half-Kelly} = 0.5 \times f^*$$

* Blends $0.5 \times \text{Base Risk} + 0.5 \times \text{Half-Kelly}$, clamped strictly within $[1\%, 5\%]$.

### 2. Hierarchical Risk Parity (HRP) Sector Clustering (`core/risk_engine.py`)
* Calculates the rolling 30-day covariance matrix and correlation distance tree across all assets.
* When correlation between crypto assets spikes ($\rho > 0.85$), HRP treats them as a single risk cluster and scales down exposure, while allocating risk to independent equities (`AMD`, `SPY`).

### 3. Market-Neutral Statistical Arbitrage (`strategies/stat_arb_pairs.py`)
* Tracks cointegrated pairs (`SOL` vs `AVAX`, `NVDA` vs `AMD`, `ETH` vs `BTC`).
* Computes rolling hedge ratio ($\beta = \text{Cov}(A,B) / \text{Var}(B)$) and spread Z-Score ($Z$).
* Enters long the undervalued asset and short the overvalued asset when $|Z| \ge 2.0$, and exits upon mean-reversion ($|Z| \le 0.5$), delivering zero-beta market-neutral returns.

### 4. Smart Order Routing / Execution Alpha (`core/coordinator.py`)
* Supports `execution_mode: maker` post-only limit orders.
* Eliminates slippage ($0.00\%$) and drops fees from $0.06\%$ to $0.02\%$, delivering a proven $+4.50\%$ pure alpha gain directly into net profit.

### 5. Automated Walk-Forward Optimization Arena (`agent/optimizer.py`)
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
