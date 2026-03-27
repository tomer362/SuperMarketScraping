"""
webapp/backend/scheduler.py
===========================
Background scrape scheduler: runs all scrapers every SCRAPE_INTERVAL_HOURS hours.
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("scheduler")


class ScrapeScheduler:
    def __init__(self) -> None:
        self.interval_hours: float = float(os.environ.get("SCRAPE_INTERVAL_HOURS", "6"))
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_run: dict | None = None

    @property
    def last_run(self) -> dict | None:
        return self._last_run

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start the background scheduler loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="scrape_scheduler")
        logger.info(
            "Scrape scheduler started — interval: %.1f hours", self.interval_hours
        )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scrape scheduler stopped.")

    async def trigger_now(self) -> dict:
        """Trigger an immediate scrape (outside the normal schedule)."""
        from scraper_runner import run_all_scrapers

        logger.info("Manual scrape triggered.")
        result = await run_all_scrapers()
        self._last_run = result
        return result

    async def _loop(self) -> None:
        from scraper_runner import run_all_scrapers

        while self._running:
            try:
                logger.info("Scheduled scrape starting…")
                self._last_run = await run_all_scrapers()
            except Exception as exc:
                logger.error("Scheduler scrape error: %s", exc, exc_info=True)

            interval_secs = self.interval_hours * 3600
            logger.info(
                "Next scrape in %.1f hours (%.0f seconds).",
                self.interval_hours,
                interval_secs,
            )
            await asyncio.sleep(interval_secs)


# Singleton instance used by FastAPI lifespan
scheduler = ScrapeScheduler()
