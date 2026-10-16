# Web4 Autonomous Trading Agent

An autonomous, self-improving quantitative trading system inspired by the Web4 / Automaton blueprint.

## Overview
* **Trading Strategy:** Pure Price & Momentum Breakout with ATR Trailing Stops.
* **Execution Mode:** Paper Trading (simulated funds) with zero real capital risk.
* **Risk Engine:** Strict hard-coded stop-loss and daily drawdown circuit breakers.
* **Self-Improvement:** Automated hypothesis generation, out-of-sample backtest validation, and Git-driven parameter updates.

## Architecture
1. **Market Ingestion:** Free OHLCV feeds (crypto/stocks).
2. **Strategy Engine:** Mathematical momentum and breakout detection.
3. **Risk Guardrails:** 1%–2% risk per position, automated trailing stops.
4. **Self-Improvement Loop:** Backtest verification before deploying changes.

## License
MIT
