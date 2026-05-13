from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable


logger = logging.getLogger("webapp.scheduler")


class RefreshScheduler:
    def __init__(
        self,
        interval_hours: float,
        refresh_callback: Callable[[str], Awaitable[dict]],
    ) -> None:
        self.interval_hours = interval_hours
        self._refresh_callback = refresh_callback
        self._task: asyncio.Task | None = None
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
        return self._lock.locked()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="catalog_refresh_scheduler")
        logger.info("Catalog refresh scheduler started with %.1f hour interval", self.interval_hours)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Catalog refresh scheduler stopped")

    async def trigger_now(self, source: str = "manual") -> dict:
        if self._lock.locked():
            return {
                "accepted": False,
                "status": "running",
                "detail": "A catalog refresh is already in progress.",
            }
        async with self._lock:
            logger.info("Triggering catalog refresh: %s", source)
            self._last_run = await self._refresh_callback(source)
            return {
                "accepted": True,
                "status": "started",
                "detail": "Catalog refresh completed.",
            }

    async def _loop(self) -> None:
        interval_seconds = self.interval_hours * 3600
        while self._running:
            if not self._lock.locked():
                try:
                    async with self._lock:
                        self._last_run = await self._refresh_callback("scheduler")
                except Exception as exc:
                    logger.error("Scheduled refresh failed: %s", exc, exc_info=True)
            await asyncio.sleep(interval_seconds)
