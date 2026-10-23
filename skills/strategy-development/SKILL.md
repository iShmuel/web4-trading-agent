---
name: strategy-development
description: Workflow for designing, implementing, and validating quantitative trading strategies in the Web4 system.
---

# Strategy Development Skill

## Purpose
Standardizes the process of introducing new strategy engines or modifying existing ones (`MomentumStrategy`, `MeanReversionStrategy`) while ensuring determinism, cost awareness, and adherence to the signal contract.

## Prerequisites
- Implementation of indicators in `core/strategy.py`.
- Adherence to `core/signals.py` dataclasses (`TradingSignal`, `SignalDirection`, `SignalStatus`).

## Workflow
1. **Define Deterministic Rules**:
   - Explicit entry conditions (trend, volatility, indicators).
   - Explicit exit conditions (stop-loss, take-profit, mean targets, maximum holding bars).
   - Explicit no-trade rationale (stale data, low volatility, thesis invalidation).
2. **Cost & Expectancy Filter**:
   - Deduct round-trip fees and slippage from estimated reward prior to emitting an actionable `ENTRY` signal.
   - Reject proposals where expected reward $\le$ expected friction costs.
3. **Signal Construction**:
   - Populate `reference_price`, `suggested_stop`, `suggested_take_profit`, `atr`, `signal_strength`, and indicator snapshot.
4. **Independent Backtesting**:
   - Run event-driven backtests over historical datasets (`research/backtest_compare.py`).
   - Validate performance across both bull and bear market regimes.
5. **Walk-Forward Validation**:
   - Test out-of-sample data (70% train / 30% test split) to reject overfitted parameter sets.

## Acceptance Criteria
- Emits standard `TradingSignal` objects.
- Does not contain hard-coded execution logic.
- Maintains positive net expectancy after modeled fees and slippage on validated assets.
