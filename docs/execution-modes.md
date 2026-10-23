# Execution Modes & Capital Protection

## Mode Separation
The system maintains strict architectural separation between **Market Data Mode** and **Execution Mode**:

| Mode Type | Active Implementation | Protection Level |
|---|---|---|
| **Market Data** | Live Exchange Polling (Binance CCXT & Yahoo Finance) | Read-only public API calls |
| **Execution** | Paper Trading (`PaperAccount`) | Simulated virtual cash |
| **Brokerage** | Real-money routing disabled | Non-routable by default |

### Non-Negotiable Safety Invariants
1. **Paper Trading is Default**: The system always executes orders virtually in `core/paper_account.py`.
2. **Real Money Disabled**: Real-money broker APIs (e.g. live Alpaca keys, live Binance trade endpoints) are disabled and cannot be activated via backtests, optimizers, or AI recommendations.
3. **Ledger Integrity**: Virtual cash, equity history, and closed trade logs are stored atomically in `data/paper_portfolio.json` with synchronous filesystem sync and `.bak` backup.
