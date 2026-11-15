"""Decentralized Async Non-Blocking Agent Execution Runner.

Implements Item 4 from FUTURE_DEVELOPMENT_NOTES.md:
Decouples heavy AI / optimization / universe discovery tasks (e.g. TradeAnalyst LLM
forensics, MultiStrategyOptimizer walk-forward sweeps, AssetSelector scans) from the
60-second real-time price & stop-loss loop in `live_paper_trader.py`.

Uses standard Python 3.10+ `concurrent.futures` (Invariant Rule #4) so the main trading
loop never stalls waiting for 20s-35s agent tasks.
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


@dataclass
class CompletedAgentTask:
    task_key: str
    started_at: str
    completed_at: str
    duration_sec: float
    success: bool
    result: Any = None
    error: Optional[str] = None


class AsyncAgentTaskRunner:
    """Non-blocking background worker pool for heavy Web4 agent tasks."""

    def __init__(self, max_workers: int = 3):
        self.max_workers = max(1, int(max_workers))
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="web4_async_agent",
        )
        self._active_futures: Dict[str, Future] = {}
        self._task_meta: Dict[str, Dict[str, Any]] = {}
        self.completed_history: List[CompletedAgentTask] = []

    def is_running(self, task_key: str) -> bool:
        """Return True if a background task with `task_key` is currently running."""
        fut = self._active_futures.get(task_key)
        return bool(fut is not None and not fut.done())

    def submit_task(
        self,
        task_key: str,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        """Submit a heavy agent task to execute asynchronously in the background.

        Returns True if scheduled, or False if `task_key` is already running.
        Never blocks the caller thread.
        """
        if self.is_running(task_key):
            return False

        self._task_meta[task_key] = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "start_perf": time.perf_counter(),
        }
        fut = self._executor.submit(fn, *args, **kwargs)
        self._active_futures[task_key] = fut
        return True

    def harvest_completed(self) -> List[CompletedAgentTask]:
        """Collect all background tasks that have finished since the last check.

        Executes in < 0.001s on the main loop thread without blocking on running tasks.
        """
        finished: List[CompletedAgentTask] = []
        done_keys = [k for k, fut in self._active_futures.items() if fut.done()]

        for key in done_keys:
            fut = self._active_futures.pop(key)
            meta = self._task_meta.pop(key, {})
            dur = max(0.0, time.perf_counter() - float(meta.get("start_perf", time.perf_counter())))
            started_at = str(meta.get("started_at", datetime.now(timezone.utc).isoformat()))
            completed_at = datetime.now(timezone.utc).isoformat()

            try:
                res = fut.result(timeout=0.0)
                rec = CompletedAgentTask(
                    task_key=key,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_sec=round(dur, 4),
                    success=True,
                    result=res,
                )
            except Exception as exc:
                rec = CompletedAgentTask(
                    task_key=key,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_sec=round(dur, 4),
                    success=False,
                    error=str(exc),
                )

            self.completed_history.append(rec)
            if len(self.completed_history) > 50:
                self.completed_history = self.completed_history[-50:]
            finished.append(rec)

        return finished

    def shutdown(self, wait: bool = False) -> None:
        """Cleanly shut down the background worker pool."""
        try:
            self._executor.shutdown(wait=wait, cancel_futures=not wait)
        except Exception:
            pass
