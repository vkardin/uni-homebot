"""Фоновая запись метрик в SQLite — основа для графиков."""

from __future__ import annotations

import asyncio
import logging
import time

from homebot.services.db import Database, MetricRow
from homebot.services.system import SystemService, SystemSnapshot

log = logging.getLogger(__name__)

_PRUNE_EVERY_SEC = 3600


def to_row(snap: SystemSnapshot, ts: int) -> MetricRow:
    root = next((d for d in snap.disks if d.mountpoint == "/"), None)
    return MetricRow(
        ts=ts,
        cpu=snap.cpu_percent,
        ram=snap.ram_percent,
        swap=snap.swap_percent,
        temp=snap.temperature.current if snap.temperature is not None else None,
        disk=root.percent if root is not None else None,
    )


class SamplerJob:
    def __init__(self, *, db: Database, system: SystemService, interval: int, retention_days: int) -> None:
        self._db = db
        self._system = system
        self._interval = interval
        self._retention = retention_days * 86400
        self._last_prune = 0.0

    async def run(self) -> None:
        log.info("Metrics sampler running (every %s s, keep %s s)", self._interval, self._retention)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Metrics sampling failed")
            await asyncio.sleep(self._interval)

    async def tick(self) -> None:
        snap = await self._system.snapshot()
        now = int(time.time())
        await self._db.add_metric(to_row(snap, now))
        if time.monotonic() - self._last_prune > _PRUNE_EVERY_SEC:
            self._last_prune = time.monotonic()
            removed = await self._db.prune_metrics(now - self._retention)
            if removed:
                log.info("Pruned %s old metric rows", removed)
