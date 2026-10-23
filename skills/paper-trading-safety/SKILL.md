---
name: paper-trading-safety
description: Safety guardrails and verification procedures to ensure live paper trading operates without real capital risk.
---

# Paper Trading Safety Skill

## Purpose
Enforces non-negotiable safety boundaries guaranteeing that live market data ingestion never routes orders to real-money brokerage endpoints.

## Core Principles
1. **Mode Isolation**:
   - The system operates strictly in simulated paper mode (`PaperAccount`).
   - Real-money brokerage connectors must remain disabled by default.
   - Profitable backtests, AI recommendations, or optimization cycles are never authorized to enable real capital trading.
2. **Persistence Protection**:
   - Never reset user portfolio cash or trade history without explicit manual `--reset` CLI invocation.
   - Maintain atomic persistence with `.bak` recovery to withstand abrupt server termination.
3. **Execution Realism**:
   - Deduct 0.06% exchange fee + 0.04% slippage model (0.20% round-trip friction).
   - Enforce realistic position sizing and cash margin availability.
