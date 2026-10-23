---
name: architecture-review
description: Guidelines and procedure for auditing and maintaining the Web4 Trading Agent modular architecture.
---

# Architecture Review Skill

## Purpose
Enforces modular system boundaries, separation of concerns, and clean architectural principles across the Web4 trading agent system without external framework coupling.

## Prerequisites
- Knowledge of the core subsystems:
  - `core/data_loader.py` — Ingestion, normalization, caching, quality checks.
  - `core/signals.py` — Universal TradingSignal contract.
  - `core/strategy.py` — Core indicator mathematics.
  - `strategies/` — Independent strategy implementations (`momentum.py`, `mean_reversion.py`).
  - `core/coordinator.py` — Signal reconciliation and conflict resolution.
  - `core/risk_engine.py` — Position sizing and portfolio circuit breakers.
  - `core/paper_account.py` — Atomic portfolio simulation and ledger.
  - `agent/` — Autonomous optimization and walk-forward validation.

## Workflow
1. **Dependency Audit**: Ensure no strategy module imports execution or paper accounting directly.
2. **Signal Flow Verification**: Verify strategies emit `TradingSignal` proposals; only `PortfolioCoordinator` routes approved proposals to `PaperAccount`.
3. **Storage Boundary Audit**: Ensure `PaperAccount` state persists atomically with backup recovery.
4. **Platform Independence**: Confirm the system runs on standard Linux/macOS/Windows with Python 3.10+ without requiring Antigravity, Gemini CLI, or proprietary agent daemons.

## Required Checks
- Verify strategies do not access `PaperAccount` directly.
- Verify risk limits cannot be relaxed by parameter optimizers.
- Check that all execution paths route through `RiskEngine`.
