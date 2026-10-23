# Contributing to Web4 Trading Agent

## Development Workflow
1. **Branching**: Develop features and strategy experiments on topic branches or verify them locally before pushing to `main`.
2. **Deterministic Indicators**: All mathematical formulas for indicators belong in `core/strategy.py`.
3. **Signal Contract**: Every strategy engine must implement the `evaluate(symbol, df, existing_position)` method and return a `TradingSignal` from `core/signals.py`.
4. **Testing Requirement**: All new features, strategies, or risk rules must include unit tests in `tests/`. Verify by running:
   ```bash
   pytest -v
   ```
5. **Benchmarks**: Ensure local computation meets sub-second p95 latency targets:
   ```bash
   python research/benchmark.py
   ```
