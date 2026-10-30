"""История метрик: периоды графиков и сводная статистика."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from homebot.services.db import Database, MetricRow

# код периода -> (секунд, подпись)
PERIODS: dict[str, tuple[int, str]] = {
    "6h": (6 * 3600, "6 часов"),
    "24h": (24 * 3600, "24 часа"),
    "7d": (7 * 86400, "7 дней"),
}
DEFAULT_PERIOD = "24h"


@dataclass(frozen=True, slots=True)
class Stats:
    samples: int
    cpu_avg: float
    cpu_max: float
    ram_avg: float
    ram_max: float
    temp_max: float | None
    temp_avg: float | None
    disk_last: float | None


def summarize(rows: Sequence[MetricRow]) -> Stats | None:
    if not rows:
        return None
    temps = [r.temp for r in rows if r.temp is not None]
    disks = [r.disk for r in rows if r.disk is not None]
    return Stats(
        samples=len(rows),
        cpu_avg=sum(r.cpu for r in rows) / len(rows),
        cpu_max=max(r.cpu for r in rows),
        ram_avg=sum(r.ram for r in rows) / len(rows),
        ram_max=max(r.ram for r in rows),
        temp_max=max(temps) if temps else None,
        temp_avg=sum(temps) / len(temps) if temps else None,
        disk_last=disks[-1] if disks else None,
    )


async def load_period(db: Database, period: str, *, now: float | None = None) -> list[MetricRow]:
    seconds = PERIODS.get(period, PERIODS[DEFAULT_PERIOD])[0]
    current = time.time() if now is None else now
    return await db.metrics_since(int(current - seconds))
