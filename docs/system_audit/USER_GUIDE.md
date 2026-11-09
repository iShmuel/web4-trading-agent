# Web4 Quantitative Trading Desk — Operator User Guide

**Document Identifier:** `DOC-USER-GUIDE-2026`  
**Purpose:** Practical Operational Manual for Managing, Monitoring, and Operating the Desk  
**Target Environment:** Linux Terminal, Virtualenv (`.venv`)  

---

## 1. Quick Command Cheat Sheet

All commands must be executed from the project root directory:
`<repo_root>`

| Action | Command Line | Description |
| :--- | :--- | :--- |
| **Start Live Paper Loop** | `.venv/bin/python live_paper_trader.py --loop --interval 60` | Starts the 60-second continuous paper trading monitoring loop. |
| **Run Single Scan Tick** | `.venv/bin/python live_paper_trader.py --once` | Scans the watchlist once, evaluates signals, and exits. |
| **Display Portfolio Status** | `.venv/bin/python live_paper_trader.py --status` | Prints current equity, cash, open positions, and realized PnL. |
| **Launch Live Dashboard** | `.venv/bin/python dashboard.py` | Opens terminal UI showing active positions and recent trades. |
| **View Decision Journal** | `.venv/bin/python live_paper_trader.py --journal` | Displays forward alpha settlement and gate audit report. |
| **List Pending Proposals** | `.venv/bin/python agent/governor.py --pending` | Lists parameter proposals awaiting human approval. |
| **Approve Proposal** | `.venv/bin/python agent/governor.py --approve PROP-ID --approver "name"` | Authorizes and atomically deploys a pending proposal. |
| **Reject Proposal** | `.venv/bin/python agent/governor.py --reject PROP-ID --approver "name"` | Disapproves and closes a pending proposal. |
| **Run Invariant Audit** | `.venv/bin/python agent/governor.py --audit` | Verifies active configuration against SEC 15c3-5 safety rules. |
| **Run Full Test Suite** | `.venv/bin/pytest tests/` | Runs all 144 automated unit and regression tests. |

---

## 2. Managing the Live Paper Trader Daemon

### 2.1 Starting the Daemon
To run the paper trader continuously in the background:
```bash
nohup .venv/bin/python live_paper_trader.py --loop --interval 60 > data/daemon.log 2>&1 &
```
The process automatically acquires `data/live_trader.lock` to prevent duplicate instances.

### 2.2 Stopping the Daemon Safely
Because `live_paper_trader.py` operates under process locking, always stop it gracefully:
```bash
# Find the PID
cat data/live_status.json | grep "pid"
# Terminate gracefully via SIGTERM
kill <PID>
```
The lock file will be released cleanly, and all open positions and cash balances will remain intact.

---

## 3. How to Monitor Trades and Read Logs

### 3.1 Live Status Snapshot (`data/live_status.json`)
The bot writes a JSON snapshot on every tick. Inspect it with:
```bash
cat data/live_status.json | jq .
```
Key fields to observe:
- `equity`: Total mark-to-market portfolio value.
- `cash`: Available uninvested capital.
- `open_positions`: Number of currently open trades.
- `assets.<SYMBOL>.action`: Latest action taken (`REJECTED`, `HOLD`, `ENTER_LONG`, etc.).
- `assets.<SYMBOL>.reason`: Exact quantitative explanation for the decision.

### 3.2 Live Log Feed (`data/live_trader.log`)
Monitor real-time scanning and execution alerts:
```bash
tail -f data/live_trader.log
```
Common log badges:
- `[TREND_EXPANSION]`: ADX $\ge 28.0$. Trend following active.
- `[RANGE_CHOP]`: ADX $< 24.0$. Mean reversion active.
- `[TRANSITION]`: $24.0 \le \text{ADX} < 28.0$. Transition state.
- `[PAPER ALERT] BUY (LONG) ORDER EXECUTED`: New simulated entry opened.
- `[PAPER ALERT] PARTIAL TAKE-PROFIT`: 50% scale-out banked.

---

## 4. Governance: Reviewing & Approving Proposals

When automated agents (Optimizer or Trade Analyst) discover an optimization:
1. **View Pending Queue:**
   ```bash
   .venv/bin/python agent/governor.py --pending
   ```
2. **Review Rationale and Metrics:**
   Inspect the proposal in `config/pending_proposals.json` to review out-of-sample backtest proofs, win rate changes, and fee reductions.
3. **Approve and Deploy:**
   ```bash
   .venv/bin/python agent/governor.py --approve PROP-A15F08C2 --approver "shmuel_operator"
   ```
4. **Instant Rollback (If Needed):**
   If a newly deployed configuration shows unexpected behavior:
   ```bash
   cp config/active_config.json.bak config/active_config.json
   ```
