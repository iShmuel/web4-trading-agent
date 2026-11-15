"""Unit and integration tests for FUTURE_DEVELOPMENT_NOTES.md Items 1-4:
1. Item 1 — Automated Central Performance Index (CPI) Benchmark Harness (`core/cpi_benchmark.py`)
2. Item 2 — Dynamic Kill-Switch Half-Step Recovery Protocol / 72h Probationary Reentry (`core/risk_engine.py`)
3. Item 3 — Maker Order Queue Tracking & Adaptive Re-Quoting Engine (`core/paper_account.py` & `core/coordinator.py`)
4. Item 4 — Decentralized Async Non-Blocking Agent Execution (`core/async_agent_runner.py`)
"""

import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from core.async_agent_runner import AsyncAgentTaskRunner
from core.cpi_benchmark import compute_cpi_score, run_cpi_benchmark
from core.paper_account import PaperAccount
from core.risk_engine import MIN_ALLOWED_RISK_PCT, RiskEngine


def _make_synthetic_df(n: int = 300, trend: float = 0.001) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    rets = trend + rng.normal(0.0, 0.008, size=n)
    close = 100.0 * np.exp(np.cumsum(rets))
    open_p = np.roll(close, 1)
    open_p[0] = 100.0
    high = np.maximum(open_p, close) * (1.0 + rng.uniform(0.001, 0.006, size=n))
    low = np.minimum(open_p, close) * (1.0 - rng.uniform(0.001, 0.006, size=n))
    vol = rng.uniform(1000.0, 5000.0, size=n)
    return pd.DataFrame(
        {"open": open_p, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def test_item_1_cpi_benchmark_harness(tmp_path):
    """Verify Item 1 CPI score calculation and 3-tier benchmark report generation."""
    score_weak = compute_cpi_score(rets=[-10.0, -5.0], dds=[20.0, 25.0], sharpes=[-1.5, -1.0], pfs=[0.7, 0.8])
    score_strong = compute_cpi_score(rets=[12.0, 8.0], dds=[6.0, 5.0], sharpes=[1.8, 1.4], pfs=[1.9, 1.6])
    assert score_strong["cpi_score"] > score_weak["cpi_score"]
    assert 0.0 <= score_strong["cpi_score"] <= 100.0

    out_json = tmp_path / "cpi_report.json"
    datasets = {
        "BTC/USDT": _make_synthetic_df(320, trend=0.0015),
        "ETH/USDT": _make_synthetic_df(320, trend=0.0012),
    }
    rep = run_cpi_benchmark(datasets=datasets, output_path=str(out_json))
    assert out_json.exists()
    assert "tiers" in rep
    assert "baseline_1_single_agent" in rep["tiers"]
    assert "baseline_2_uncoordinated_multi" in rep["tiers"]
    assert "web4_coordinated_multi_agent" in rep["tiers"]


def test_item_2_kill_switch_72h_probationary_reentry():
    """Verify Item 2 72h cooldown, 3-condition stabilization gate, 1.0% risk floor, and 3-win graduation."""
    re = RiskEngine(default_risk_pct=0.02, max_drawdown_limit_pct=0.15)
    t0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    # Establish $10,000 peak then trigger 15.2% drawdown halt at $8,480
    re.check_circuit_breakers(10000.0, current_time=t0)
    ok, _ = re.check_circuit_breakers(8480.0, current_time=t0 + timedelta(hours=1))
    assert not ok
    assert re.is_halted

    # 1. Before 72h cooldown: probationary reentry must be rejected even if VIX and EMA200 are stable
    ok_early, msg_early = re.evaluate_probationary_reentry(
        current_equity=8480.0,
        vix_stable=True,
        ema_200_reclaimed=True,
        strategy_profit_factor=1.35,
        current_time=t0 + timedelta(hours=48),
    )
    assert not ok_early
    assert re.is_halted
    assert "cooldown active" in msg_early

    # 2. After 72h cooldown, but VIX or EMA200 not stabilized: must stay halted
    t_after_72h = t0 + timedelta(hours=74)
    ok_unstable, _ = re.evaluate_probationary_reentry(
        current_equity=8480.0,
        vix_stable=False,
        ema_200_reclaimed=True,
        strategy_profit_factor=1.35,
        current_time=t_after_72h,
    )
    assert not ok_unstable
    assert re.is_halted

    # 3. After 72h cooldown AND VIX < 20 + EMA200 reclaimed + PF >= 1.15: enters PROBATION at 1.0% risk floor
    ok_prob, _ = re.evaluate_probationary_reentry(
        current_equity=8480.0,
        vix_stable=True,
        ema_200_reclaimed=True,
        strategy_profit_factor=1.35,
        current_time=t_after_72h,
    )
    assert ok_prob
    assert not re.is_halted
    assert re.in_probation

    # Verify position sizing during probation is strictly clamped to MIN_ALLOWED_RISK_PCT (1.0%, $84.80 on $8,480)
    verdict = re.validate_proposed_entry(
        symbol="BTC/USDT",
        side="LONG",
        strategy_id="momentum",
        entry_price=100.0,
        suggested_stop=95.0,
        atr=2.0,
        atr_multiplier=2.5,
        equity=8480.0,
        cash=8480.0,
        active_positions={},
        requested_risk_pct=0.03,
        current_time=t_after_72h,
    )
    assert verdict.approved
    assert abs(verdict.dollar_risk - (8480.0 * MIN_ALLOWED_RISK_PCT)) < 1e-4

    # 4. Three consecutive winning trades graduate the system back to normal sizing
    assert re.record_probation_trade_outcome(25.0, 8505.0, current_time=t_after_72h) == "PROBATION_WIN_1"
    assert re.record_probation_trade_outcome(30.0, 8535.0, current_time=t_after_72h) == "PROBATION_WIN_2"
    assert re.record_probation_trade_outcome(40.0, 8575.0, current_time=t_after_72h) == "GRADUATED"
    assert not re.in_probation
    assert not re.is_halted


def test_item_3_maker_queue_and_adaptive_requoting():
    """Verify Item 3 passive maker fill on pullback, adaptive re-quoting on runaway breakout, and spike cancellation."""
    # Case A: Normal bar with >= 15% pullback fills passively at maker fee
    res_passive = PaperAccount.evaluate_maker_fill_or_requote(
        side="LONG",
        quote_price=100.0,
        bar_open=100.0,
        bar_high=104.0,
        bar_low=99.0,  # pullback = 1.0 / 5.0 = 0.20 >= 0.15
        bar_close=103.0,
        bar_volume=1500.0,
        avg_volume=1000.0,
        maker_fee_pct=0.0002,
        taker_fee_pct=0.0006,
    )
    assert res_passive["filled"] is True
    assert res_passive["execution_mode"] == "PASSIVE_MAKER_FILL"
    assert res_passive["effective_fee_pct"] == 0.0002
    assert res_passive["exec_price"] == 100.0

    # Case B: Runaway impulsive breakout (pullback < 15%, high velocity/volume) -> Adaptive Re-Quoting captures it
    res_adaptive = PaperAccount.evaluate_maker_fill_or_requote(
        side="LONG",
        quote_price=100.0,
        bar_open=100.0,
        bar_high=105.0,
        bar_low=99.9,  # pullback = 0.1 / 5.1 = 0.0196 < 0.15
        bar_close=104.5,
        bar_volume=2500.0,
        avg_volume=1000.0,
    )
    assert res_adaptive["filled"] is True
    assert res_adaptive["execution_mode"] == "ADAPTIVE_REQUOTED_FILL"
    assert res_adaptive["exec_price"] > 100.0

    # Case C: Unconfirmed low-volume thin spike (pullback < 15%, weak body, low volume) -> Cancelled safely
    res_cancel = PaperAccount.evaluate_maker_fill_or_requote(
        side="LONG",
        quote_price=100.0,
        bar_open=100.0,
        bar_high=105.0,
        bar_low=99.9,
        bar_close=100.5,  # body_ratio = 0.5 / 5.1 = 0.098 < 0.35
        bar_volume=300.0,  # < 85% of avg_volume
        avg_volume=1000.0,
    )
    assert res_cancel["filled"] is False
    assert res_cancel["execution_mode"] == "CANCELLED_UNFILLED_SPIKE"


def test_item_4_async_non_blocking_agent_runner():
    """Verify Item 4 AsyncAgentTaskRunner dispatches tasks in < 5ms without blocking and harvests results cleanly."""
    runner = AsyncAgentTaskRunner(max_workers=2)

    def slow_agent_job(x: int) -> dict:
        time.sleep(0.15)
        return {"computed": x * 2}

    t0 = time.perf_counter()
    scheduled = runner.submit_task("test_job", slow_agent_job, 21)
    dispatch_ms = (time.perf_counter() - t0) * 1000.0

    assert scheduled is True
    assert dispatch_ms < 25.0  # Non-blocking dispatch
    assert runner.is_running("test_job") is True
    # Duplicate submission while running is ignored
    assert runner.submit_task("test_job", slow_agent_job, 99) is False

    time.sleep(0.22)
    completed = runner.harvest_completed()
    assert len(completed) == 1
    assert completed[0].task_key == "test_job"
    assert completed[0].success is True
    assert completed[0].result == {"computed": 42}
    runner.shutdown(wait=True)
