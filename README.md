# Web4 Autonomous Trading Agent

An autonomous, self-improving quantitative trading system inspired by the Web4 / Automaton blueprint.

## Overview
* **Trading Strategy:** Pure Price & Momentum Breakout with Dynamic ATR Trailing Stops.
* **Execution Mode:** Paper Trading (simulated funds) with zero real capital risk.
* **Risk Engine:** Strict hard-coded stop-loss and position sizing dynamically bounded between **1% and 5% account risk per trade**.
* **Self-Improvement:** Automated parameter hypothesis generation, out-of-sample walk-forward backtest validation, and Git-driven configuration updates.

## Architecture & Pillars
1. **Market Ingestion (`core/data_loader.py`):** Free OHLCV feeds (crypto/stocks via Binance/Alpaca) with local caching.
2. **Strategy Engine (`core/strategy.py`):** Donchian channel breakout combined with 200 EMA macro trend filter.
3. **Risk Guardrails (`core/risk_engine.py`):**
   * Dynamic position sizing based on volatility: $\text{Size} = \frac{\text{Equity} \times \text{Risk\%}}{\text{Multiplier} \times \text{ATR}}$.
   * Non-negotiable risk cap: **1% to 5% maximum capital risk per trade**.
   * Automatic ratcheting trailing stops to lock in winning trends.
   * Daily account circuit breaker.
4. **Self-Improvement Loop (`agent/`):**
   * In-sample training vs. out-of-sample validation to prevent curve-fitting/overfitting.
   * Automated Git commits for verified parameter improvements.

## License
MIT
