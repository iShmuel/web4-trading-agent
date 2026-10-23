---
name: performance-engineering
description: Methodology for profiling and measuring latency and throughput across quantitative computation pipelines.
---

# Performance Engineering Skill

## Purpose
Ensures local computation remains sub-second across universe scans, measuring processing speed independently from trading profitability.

## Provisional Targets
- 100 symbols: p95 latency < 1,000 ms
- 500 symbols: p95 latency < 3,000 ms
- 1,000 symbols: p95 latency < 5,000 ms

## Execution
Run the automated benchmark suite:
```bash
python research/benchmark.py
```
This suite benchmarks the complete local pipeline: feature engineering, dual-strategy signal generation, coordinator reconciliation, risk validation, and paper accounting.
