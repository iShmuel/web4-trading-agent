# Engineering Guidelines for Autonomous AI Agents

This repository is designed to be maintained, inspected, and improved by autonomous coding agents and human engineers alike.

## Invariant Rules for AI Agents
1. **Never Enable Real-Money Trading**: Execution mode must remain strictly simulated paper trading (`PaperAccount`).
2. **Preserve User Data**: Never overwrite `data/paper_portfolio.json` or reset virtual balances unless instructed by an explicit CLI `--reset` argument.
3. **Preserve 1%-5% Risk Bounds**: Position sizing in `core/risk_engine.py` is hard-coded between 1% and 5% account equity. No optimizer or AI mutation may loosen this constraint.
4. **Platform Independence**: Do not add dependencies on Antigravity CLI, Gemini CLI, or proprietary agent harnesses. Code must execute using standard Python 3.10+.
5. **Evidence-Based Profitability**: Do not declare a strategy profitable without empirical backtest and cost-adjusted metrics (fees + slippage). Report underperforming assets honestly.
