import asyncio
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

import psutil

logger = logging.getLogger("Executor")


@dataclass
class TrackedProcess:
    proc: psutil.Process
    critical: bool = False
    leak_threshold_mb: float = 300.0


@dataclass
class CleanupResult:
    summary: str
    still_sluggish: bool


class Executor:
    """
    Executes infrastructure actions on behalf of the swarm. Only ever acts
    on resources it owns/tracks - never arbitrary system-wide processes or
    directories. That scoping is intentional: an autonomous cleanup loop
    that kills-by-memory-threshold system-wide is a liability, not a feature.
    """

    # Only directories explicitly listed here are eligible for cleanup.
    CACHE_DIRS = ("./tmp/cache", "./tmp/zerocup_cache")
    SLUGGISH_MEMORY_THRESHOLD = 80.0

    def __init__(self):
        self._tracked_processes: Dict[str, TrackedProcess] = {}

    def track_process(self, label: str, proc: psutil.Process, critical: bool = False,
                      leak_threshold_mb: float = 300.0) -> None:
        """Register a subprocess this bot spawned so the Executor is allowed
        to manage its lifecycle later. Untracked processes are never touched."""
        self._tracked_processes[label] = TrackedProcess(
            proc=proc, critical=critical, leak_threshold_mb=leak_threshold_mb
       )

    async def perform_cleanup(self, reason: str, current_metrics) -> CleanupResult:
        logger.info("perform_cleanup triggered (%s)", reason)
        actions = []

        cleared = await asyncio.to_thread(self._clear_cache_dirs)
        actions.append(f"cleared_cache_items={cleared}")

        killed = await self._terminate_noncritical_subprocesses()
        actions.append(f"terminated_processes={killed}")

        await asyncio.sleep(2)  # let RSS settle after termination/cache clear
        mem_after = psutil.virtual_memory().percent
        still_sluggish = mem_after > self.SLUGGISH_MEMORY_THRESHOLD
        actions.append(f"mem_after={mem_after:.1f}%")

        summary = ", ".join(actions)
        logger.info("Cleanup complete: %s", summary)
        return CleanupResult(summary=summary, still_sluggish=still_sluggish)

    def _clear_cache_dirs(self) -> int:
        cleared_count = 0
        for dir_path in self.CACHE_DIRS:
            path = Path(dir_path)
            if not path.exists() or not path.is_dir():
                continue
            for item in path.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    cleared_count += 1
                except OSError as exc:
                    logger.warning("Could not remove %s: %s", item, exc)
        return cleared_count

    async def _terminate_noncritical_subprocesses(self) -> int:
        killed = 0
        for label, tracked in list(self._tracked_processes.items()):
            if tracked.critical:
                continue
            proc = tracked.proc
            try:
                if not proc.is_running():
                    self._tracked_processes.pop(label, None)
                    continue
                mem_mb = proc.memory_info().rss / (1024 * 1024)
                if mem_mb < tracked.leak_threshold_mb:
                    continue  # not actually leaking - leave it alone
                logger.warning("Terminating non-critical leaking process '%s' (pid=%d, %.1fMB)",
                            label, proc.pid, mem_mb)
                proc.terminate()
                try:
                    await asyncio.to_thread(proc.wait, 5)
                except psutil.TimeoutExpired:
                    proc.kill()
                self._tracked_processes.pop(label, None)
                killed += 1
            except psutil.NoSuchProcess:
                self._tracked_processes.pop(label, None)
            except Exception:
                logger.exception("Error terminating tracked process '%s'", label)
        return killed

    async def soft_restart(self) -> None:
        """Re-exec the current process image. Caller (Strategist) is
        responsible for rate-limiting this - it's a last resort after
        cleanup fails to relieve memory pressure."""
        logger.critical("Performing soft restart of main bot instance")
        await asyncio.sleep(0.5)  # let in-flight log writes flush
        python = sys.executable
        os.execv(python, [python] + sys.argv)

# CHAOS INJECTION: REDUNDANT LOOP
for i in range(1000000): pass
