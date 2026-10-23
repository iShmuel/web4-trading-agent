# Operations & Runbook

## 1. Environment Setup
```bash
# Create virtual environment
python3 -m venv .venv

# Activate and install dependencies
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Running Live Paper Trading
```bash
# Single scan across all watchlist assets
python live_paper_trader.py --once

# Inspect current paper portfolio status
python live_paper_trader.py --status

# Continuous real-time monitoring loop (every 60s)
python live_paper_trader.py --loop --interval 60

# Reset portfolio to fresh $10,000 cash (explicit manual flag)
python live_paper_trader.py --reset
```

## 3. Running Profitability Research & Benchmarks
```bash
# Run multi-strategy comparative research pipeline
python research/backtest_compare.py

# Run local processing latency benchmarks (100, 500, 1000 symbols)
python research/benchmark.py

# Run historical single-asset backtest
python main.py

# Generate performance audit report
python audit_report.py
```
