from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


logger = logging.getLogger("webapp.scheduler")


@dataclass(frozen=True)
class RefreshSchedule:
    refresh_kind: str
    interval_hours: float


class RefreshScheduler:
    def __init__(
        self,
        price_interval_hours: float,
        deals_interval_hours: float,
        refresh_callback: Callable[[str, str], Awaitable[dict]],
    ) -> None:
        self.price_interval_hours = price_interval_hours
        self.deals_interval_hours = deals_interval_hours
        self.interval_hours = price_interval_hours
        self._refresh_callback = refresh_callback
        self._tasks: list[asyncio.Task] = []
        self._refresh_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._running = False
        self._last_run: dict | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_run(self) -> dict | None:
        return self._last_run

    @property
    def refresh_in_progress(self) -> bool:
        return self._lock.locked() or (
            self._refresh_task is not None and not self._refresh_task.done()
        )

    async def _run_refresh(self, source: str, refresh_kind: str) -> None:
        async with self._lock:
            try:
                self._last_run = await self._refresh_callback(source, refresh_kind)
            except Exception as exc:
                logger.error("%s catalog refresh failed: %s", refresh_kind, exc, exc_info=True)
                self._last_run = {
                    "source": source,
                    "refresh_kind": refresh_kind,
                    "status": "failed",
                    "errors": [str(exc)],
                }

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        schedules = [
            RefreshSchedule("prices", self.price_interval_hours),
            RefreshSchedule("deals", self.deals_interval_hours),
        ]
        self._tasks = [
            asyncio.create_task(self._loop(schedule), name=f"catalog_refresh_{schedule.refresh_kind}_scheduler")
            for schedule in schedules
        ]
        logger.info(
            "Catalog refresh scheduler started: prices every %.1f hours, deals every %.1f hours",
            self.price_interval_hours,
            self.deals_interval_hours,
        )

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            if task and not task.done():
                task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        logger.info("Catalog refresh scheduler stopped")

    async def trigger_now(self, refresh_kind: str = "prices", source: str = "manual") -> dict:
        if refresh_kind not in {"prices", "deals"}:
            raise ValueError(f"Unsupported refresh kind: {refresh_kind}")
        if self.refresh_in_progress:
            return {
                "accepted": False,
                "status": "running",
                "detail": "A catalog refresh is already in progress.",
            }
        logger.info("Triggering %s catalog refresh: %s", refresh_kind, source)
        self._refresh_task = asyncio.create_task(
            self._run_refresh(source, refresh_kind),
            name=f"catalog_refresh_{refresh_kind}_{source}",
        )
        return {
            "accepted": True,
            "status": "started",
            "detail": f"Catalog {refresh_kind} refresh started.",
        }

    async def _loop(self, schedule: RefreshSchedule) -> None:
        interval_seconds = schedule.interval_hours * 3600
        while self._running:
            if not self.refresh_in_progress:
                await self._run_refresh("scheduler", schedule.refresh_kind)
            await asyncio.sleep(interval_seconds)
