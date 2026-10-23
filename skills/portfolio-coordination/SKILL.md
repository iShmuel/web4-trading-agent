---
name: portfolio-coordination
description: Rules for multi-strategy coordination, signal reconciliation, and conflict resolution across assets.
---

# Portfolio Coordination Skill

## Purpose
Governs how multiple autonomous strategies (Momentum, Mean Reversion) interact within a single shared capital pool without double-counting cash, colliding over positions, or exceeding exposure limits.

## Coordination Rules
1. **Conflicting Signals (Opposite Directions)**:
   - If Strategy A proposes `LONG` and Strategy B proposes `SHORT` on the same asset at the same timestamp, the coordinator rejects both proposals. A conflict indicates market ambiguity.
2. **Concordant Signals (Same Direction)**:
   - If both strategies propose `LONG` (or both propose `SHORT`), the coordinator selects the proposal with the higher `signal_strength` and allocates capital once.
3. **Cross-Strategy Position Protection**:
   - If a position is already open for symbol $S$ under Strategy A, Strategy B cannot open an opposing position on symbol $S$.
4. **Orderly Exit Handling**:
   - Exit proposals emitted by the owning strategy are executed immediately to preserve capital and honor stops.
