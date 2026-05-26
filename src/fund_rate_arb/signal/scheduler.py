"""Polling scheduler: per-exchange cadence."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class PollScheduler:
    """Run tasks at different intervals per exchange."""

    def __init__(
        self,
        hl_interval: int = 3600,
        bn_interval: int = 28800,
    ):
        self.hl_interval = hl_interval
        self.bn_interval = bn_interval
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def run(self, hl_callback, bn_callback) -> None:
        """Start polling both exchanges concurrently."""
        self._running = True
        hl_task = asyncio.create_task(self._loop("hyperliquid", self.hl_interval, hl_callback))
        bn_task = asyncio.create_task(self._loop("binance", self.bn_interval, bn_callback))
        self._tasks = [hl_task, bn_task]

        try:
            await asyncio.gather(hl_task, bn_task, return_exceptions=True)
        except asyncio.CancelledError:
            self._running = False
            raise

    async def _loop(self, name: str, interval: int, callback) -> None:
        logger.info("Scheduler started: %s every %ds", name, interval)
        while self._running:
            try:
                await callback()
            except Exception:
                logger.exception("Callback error: %s", name)
            await asyncio.sleep(interval)

    def stop(self):
        self._running = False
        for task in self._tasks:
            task.cancel()
