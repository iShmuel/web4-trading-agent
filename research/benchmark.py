"""Performance Engineering Benchmark Suite.

Measures local computation latency for the complete quantitative pipeline:
1. Feature & Indicator computation (Bollinger Bands, RSI, ATR, EMA, Donchian)
2. Strategy evaluation (Momentum & Mean Reversion)
3. Coordinator signal reconciliation & conflict resolution
4. Central risk management validation
5. Paper account state update & persistence

Evaluates against local targets:
- 100 symbols: Target p95 < 1.0s
- 500 symbols: Target p95 < 3.0s
- 1,000 symbols: Target p95 < 5.0s
"""

import json
import sys
import time
from pathlib import Path
from typing import Dict, Any, List
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paper_account import PaperAccount
from core.risk_engine import RiskEngine
from core.coordinator import PortfolioCoordinator


def generate_synthetic_ohlcv(bars: int = 150, start_price: float = 100.0) -> pd.DataFrame:
    """Generate deterministic synthetic OHLCV data for stress benchmarking."""
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=bars, freq="1h", tz="UTC")
    returns = np.random.normal(0.0002, 0.015, size=bars)
    prices = start_price * np.exp(np.cumsum(returns))

    highs = prices * (1.0 + np.abs(np.random.normal(0, 0.005, size=bars)))
    lows = prices * (1.0 - np.abs(np.random.normal(0, 0.005, size=bars)))
    opens = prices * (1.0 + np.random.normal(0, 0.002, size=bars))
    closes = prices
    volumes = np.random.uniform(500, 5000, size=bars)

    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=dates,
    )
    df.index.name = "timestamp"
    return df


def benchmark_universe(
    num_symbols: int,
    iterations: int = 5,
    bars_per_symbol: int = 150,
) -> Dict[str, Any]:
    """Benchmark full pipeline across a universe of N symbols."""
    # Pre-generate data
    sample_df = generate_synthetic_ohlcv(bars=bars_per_symbol)
    universe = {f"SYM_{i:04d}": sample_df.copy() for i in range(num_symbols)}

    durations: List[float] = []

    for _ in range(iterations):
        # Fresh isolated instances for each run
        account = PaperAccount(initial_capital=100000.0)
        risk = RiskEngine()
        config = {
            "account_risk_pct": 0.02,
            "breakout_lookback": 24,
            "ema_trend_filter": 50,
            "atr_period": 14,
            "atr_multiplier": 2.5,
            "fee_pct": 0.0006,
            "slippage_pct": 0.0004,
            "bb_period": 20,
            "rsi_period": 14,
        }
        coordinator = PortfolioCoordinator(account, risk, config)

        t0 = time.perf_counter()
        report = coordinator.run_cycle(universe)
        t1 = time.perf_counter()

        durations.append(t1 - t0)

    durations_ms = [d * 1000.0 for d in durations]
    p50 = float(np.percentile(durations_ms, 50))
    p95 = float(np.percentile(durations_ms, 95))
    p99 = float(np.percentile(durations_ms, 99))
    avg = float(np.mean(durations_ms))
    throughput = num_symbols / (np.mean(durations))

    return {
        "num_symbols": num_symbols,
        "iterations": iterations,
        "avg_latency_ms": round(avg, 2),
        "p50_latency_ms": round(p50, 2),
        "p95_latency_ms": round(p95, 2),
        "p99_latency_ms": round(p99, 2),
        "throughput_symbols_per_sec": round(throughput, 1),
    }


def run_all_benchmarks():
    print("=" * 72)
    print("       WEB4 TRADING AGENT: LOCAL PERFORMANCE BENCHMARK SUITE")
    print("=" * 72)
    print("Measuring end-to-end local computation per cycle:")
    print("  - Feature calculation (ATR, EMA, Donchian, BB, RSI, ADX)")
    print("  - Dual strategy evaluation (Momentum + Mean Reversion)")
    print("  - Conflict resolution & signal reconciliation")
    print("  - Central risk validation & portfolio position sizing")
    print("-" * 72)

    targets = [
        (100, 1000.0),   # 100 symbols < 1000ms
        (500, 3000.0),   # 500 symbols < 3000ms
        (1000, 5000.0),  # 1000 symbols < 5000ms
    ]

    results = []
    for count, target_ms in targets:
        print(f"Benchmarking universe of {count} symbols (Target p95: < {target_ms/1000:.1f}s)...")
        res = benchmark_universe(num_symbols=count, iterations=5)
        passed = res["p95_latency_ms"] <= target_ms
        status_str = "PASSED" if passed else "FAILED"

        print(f"  Result ({count} symbols):")
        print(f"    p50: {res['p50_latency_ms']} ms | p95: {res['p95_latency_ms']} ms | p99: {res['p99_latency_ms']} ms")
        print(f"    Throughput: {res['throughput_symbols_per_sec']} symbols/second")
        print(f"    Target Check: [{status_str}] (p95 {res['p95_latency_ms']} ms vs {target_ms} ms)")
        print("-" * 72)
        results.append({**res, "target_ms": target_ms, "passed": passed})

    output_file = PROJECT_ROOT / "data" / "benchmark_results.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"Benchmark results saved to {output_file.name}")
    print("=" * 72)


if __name__ == "__main__":
    run_all_benchmarks()
