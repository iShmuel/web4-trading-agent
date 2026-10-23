---
name: risk-management
description: Centralized risk validation, volatility-based sizing, and circuit breakers.
---

# Risk Management Skill

## Purpose
Enforces non-negotiable quantitative risk constraints across all trading activities.

## Non-Negotiable Rules
1. **Van Tharp Position Sizing**:
   $$\text{Units} = \frac{\text{Equity} \times \text{Risk \%}}{\text{ATR} \times \text{Multiplier}}$$
   Account risk per trade is strictly clamped between **1.0% and 5.0%**.
2. **Portfolio Constraints**:
   - Total Portfolio Exposure Cap: $\le 80\%$ of equity.
   - Single Asset Exposure Cap: $\le 35\%$ of equity.
   - Per-Strategy Allocation Cap: $\le 60\%$ of equity.
   - Max Concurrent Open Positions: default 3.
3. **Circuit Breakers**:
   - **Daily Loss Breaker**: If equity drops by $\ge 5\%$ from daily opening equity, the emergency kill switch triggers, halting all new entries for the remainder of the session.
   - **Max Drawdown Breaker**: If equity drops by $\ge 15\%$ from high-water mark, trading halts immediately.
