# Testing & Quality Assurance Guide

## Automated Test Suite
The repository includes a comprehensive, network-independent test suite covering the entire quantitative stack.

### Running Tests
Execute pytest using the project's virtual environment:
```bash
.venv/bin/pytest -v
```

### Coverage Areas
1. **Signal Contract (`tests/test_signals.py`)**:
   - `TradingSignal` instantiation, serialization to dictionary, cost estimation, actionability rules.
2. **Strategy Engines (`tests/test_strategies.py`)**:
   - Momentum breakout generation, short breakdowns, ATR stop exits.
   - Mean reversion oversold signals, ADX trend suppression, SMA target exits.
3. **Coordinator (`tests/test_coordinator.py`)**:
   - Conflict resolution for opposing signals (thesis clash rejection).
   - Selection of highest signal strength for matching signals.
   - Cross-strategy position safety.
4. **Risk Controls (`tests/test_risk_circuit_breakers.py`)**:
   - 1%-5% Van Tharp risk clamping.
   - Daily loss limit circuit breaker.
   - Max drawdown circuit breaker.
   - Single-asset exposure scaling.
5. **Paper Account & Execution (`tests/test_paper_account_advanced.py`)**:
   - Long & short position accounting.
   - Partial take-profit scale-out.
   - Atomic file writes and automatic `.bak` recovery.
6. **Data Quality (`tests/test_data_quality.py`)**:
   - Crypto vs. stock symbol classification.
   - Staleness detection and price anomaly rejection.
