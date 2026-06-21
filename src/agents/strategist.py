import asyncio
import logging
import time
from collections import deque
from dataclasss import dataclasss
from datetime import datetime, timezone
from typing import Deque, List, Optional, Protocol

import psutil

from .executor import Executor

logger = logging.getLogger("Strategist")

MISSION_LOG_PATH = "mission_status.log"


@dataclass
class MetricSample:
    timestamp: float
    memory_percent: float
    cpu_percent: float


class SentinelLogSource(Protocol):
    """Adapter interface so Strategist doesn't need to know Sentinel's
    internals directly. Wire this to your actual Sentinel agent - e.g.
    have Sentinel push samples somewhere (Redis stream, shared deque) and
    implement recent_samples() to read from it."""
    async def recent_samples(self, window_seconds: int) -> List[MetricSample]: ...


class PsutilMetricSource:
    """Default fallback - reads live system metrics directly via psutil if
    no Sentinel adapter is wired up. Fully functional standalone."""

    def __init__(self, history_size: int = 60):
        self._history: Deque[MetricSample] = deque(maxlen=history_size)

    async def recent_samples(self, window_seconds: int) -> List[MetricSample]:
        now = time.time()
        sample = MetricSample(
            timestamp=now,
            memory_percent=psutil.virtual_memory().percent,
            cpu_percent=psutil.cpu_percent(interval=None),
        )
        self._history.append(sample)
        return [s for s in self._history if now - s.timestamp <= window_seconds]


class Strategist:
    """
    Periodically evaluates system health (via Sentinel logs or a psutil
    fallback) and autonomously triggers OPTIMIZATION_PTOTOCOL on sustained
    high load or a memory-leak trend. Runs as a background asyncio task so
    it never blocks normal user interactionhandling.
    """

    MEMORY_THRESHOLD_PERCENT = 85.0
    LEAK_SLOPE_THRESHOLD_PCT_PER_MIN = 0.5
    CHECK_INTERVAL_SECONDS = 30
    EVAL_WINDOW_SECONDS = 300
    MIN_SAMPLES_FOR_TREND = 5
    RESTART_COOLDOWN_SECONDS = 600  # don't soft-restart more than once per 10 min

    def __init__(self, executor: Executor, metric_source: Optional[SentinelLogSource] = None):
        self._executor = executor
        self._metric_source = metric_source or PsutilMetricSource()
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._last_restart_ts: float = 0.0

    def start(self) -> None:
        """Launch the monitoring loop as a background task - non-blocking,
        runs alongside normal Telegram update handling."""
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._monitor_loop())
            logger.info("Strategist monitoring loop started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            await self._task

    async def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._evaluate_once()
            except Exception:
                logger.exception("Strategist evaluation cycle failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.CHECK_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _evaluate_once(self) -> None:
        samples = await self._metric_source.recent_samples(self.EVAL_WINDOW_SECONDS)
        if not samples:
            return

        latest = samples[-1]
        sustained_high_load = latest.memory_percent > self.MEMORY_THRESHOLD_PERCENT
        leak_trend = self._detect_leak_trend(samples)

        if sustained_high_load or leak_trend:
            reason = "sustained_high_memory" if sustained_high_load else "memory_leak_trend"
            logger.warning("Trigger condition met: %s (mem=%.1f%)", reason, latest.memory_percent)
            await self._trigger_optimization(reason, latest)

    def _detect_leak_trend(self, samples: List[MetricSample]) -> bool:
        """Linear regression slope of memory% over time - flags a
        sustained upward trend, not just a momentary spike."""
        if len(samples) < self.MIN_SAMPLES_FOR_TREND:
            return False
        xs = [s.timestamp - samples[0].timestamp for s in samples]
        ys = [s.memory_percent for s in samples]
        n = len(xs)
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        denom = sum((x - mean_x) ** 2 for x in xs)
        if denom == 0:
            return False
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
        return (slope * 60) > self.LEAK_SLOPE_THRESHOLD_PCT_PER_MIN

    async def _trigger_optimization(self, reason: str, latest: MetricSample) -> None:
        result = await self._executor.perform_cleanup(reason=reason, current_metrics=latest)
        await self._write_mission_log("OPTIMIZATION_PROTOCOL", result.summary)

        if result.still_sluggish and self._restart_allowed():
            self._last_restart_ts = time.time()
            await self._write_mission_log(
                "SOFT_RESTART", "Cleanup did not resolve load - initiating soft restart"
            )
            await self._executor.soft_restart()

    def _restart_allowed(self) -> bool:
        return (time.time() - self._last_restart_ts) > self.RESTART_COOLDOWN_SECONDS

    async def _write_mission_log(self, action: str, result: str) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        line = f"[{ts}] [{action}] [{result}]\n"
        try:
            await asyncio.to_thread(self._append_log, line)
        except Exception:
            logger.exception("Failed to write mission_status.log")

    @staticmethod
    def _append_log(line: str) -> None:
        with open(MISSION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
