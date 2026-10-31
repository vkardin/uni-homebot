"""📈 Отчёты о сервере за неделю, месяц и год.

Считаются по часовым итогам (`metrics_hourly`): подробные метрики живут HISTORY_RETENTION_DAYS (по умолчанию 30 дней),
а часовые — 400 дней, поэтому годовой отчёт доступен. Каждый отчёт сравнивается с прошлым таким же периодом.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import datetime, tzinfo
from typing import Any

from homebot.services.db import Database, HourRow

PERIODS: dict[str, tuple[int, str]] = {
    "week": (7 * 86400, "неделю"),
    "month": (30 * 86400, "месяц"),
    "year": (365 * 86400, "год"),
}


@dataclass(frozen=True, slots=True)
class Report:
    period: str
    hours: int  # сколько часов есть в истории за период
    cpu_avg: float
    cpu_max: float
    ram_avg: float
    ram_max: float
    temp_max: float | None
    disk_start: float | None
    disk_end: float | None
    busiest: int | None  # начало самого нагруженного часа (unix)
    prev_cpu_avg: float | None
    prev_ram_avg: float | None
    coverage: float  # доля часов периода, за которые есть данные
    points: list[tuple[int, float, float]]  # (день, CPU, RAM) — средние по дням для графика


def _days(rows: list[HourRow]) -> list[tuple[int, float, float]]:
    buckets: dict[int, list[HourRow]] = {}
    for r in rows:
        buckets.setdefault(r.ts - r.ts % 86400, []).append(r)
    return [
        (day, round(sum(x.cpu_avg for x in rs) / len(rs), 1), round(sum(x.ram_avg for x in rs) / len(rs), 1))
        for day, rs in sorted(buckets.items())
    ]


def build(rows: list[HourRow], prev: list[HourRow], period: str) -> Report | None:
    if not rows:
        return None
    seconds = PERIODS[period][0]
    disks = [r.disk for r in rows if r.disk is not None]
    temps = [r.temp_max for r in rows if r.temp_max is not None]
    busiest = max(rows, key=lambda r: r.cpu_avg)
    return Report(
        period=period,
        hours=len(rows),
        cpu_avg=round(sum(r.cpu_avg for r in rows) / len(rows), 1),
        cpu_max=round(max(r.cpu_max for r in rows), 1),
        ram_avg=round(sum(r.ram_avg for r in rows) / len(rows), 1),
        ram_max=round(max(r.ram_max for r in rows), 1),
        temp_max=round(max(temps), 1) if temps else None,
        disk_start=disks[0] if disks else None,
        disk_end=disks[-1] if disks else None,
        busiest=busiest.ts,
        prev_cpu_avg=round(sum(r.cpu_avg for r in prev) / len(prev), 1) if prev else None,
        prev_ram_avg=round(sum(r.ram_avg for r in prev) / len(prev), 1) if prev else None,
        coverage=round(min(1.0, len(rows) * 3600 / seconds), 2),
        points=_days(rows),
    )


async def make(db: Database, period: str, *, now: float | None = None) -> Report | None:
    if period not in PERIODS:
        raise ValueError(period)
    current = int(time.time() if now is None else now)
    await db.rollup_hours(current)  # чтобы последние часы тоже попали в отчёт
    seconds = PERIODS[period][0]
    rows = await db.hourly_since(current - seconds, current)
    prev = await db.hourly_since(current - 2 * seconds, current - seconds)
    return build(rows, prev, period)


def _delta(now: float, before: float | None) -> str:
    if before is None:
        return ""
    diff = now - before
    if abs(diff) < 1:
        return ", как и раньше"
    return f", {'+' if diff > 0 else '−'}{abs(diff):.0f} п.п. к прошлому периоду"


def render(rep: Report | None, period: str, tz: tzinfo | None = None) -> str:
    title = PERIODS.get(period, PERIODS["week"])[1]
    if rep is None:
        return f"📈 <b>Отчёт за {title}</b>\n\nИстории пока нет: метрики копятся с запуска бота, первый час появится через час."
    lines = [f"📈 <b>Отчёт за {title}</b>", ""]
    lines.append(f"🧠 CPU: в среднем {rep.cpu_avg:.0f}%{_delta(rep.cpu_avg, rep.prev_cpu_avg)}; пик {rep.cpu_max:.0f}%")
    lines.append(f"💾 Память: в среднем {rep.ram_avg:.0f}%{_delta(rep.ram_avg, rep.prev_ram_avg)}; пик {rep.ram_max:.0f}%")
    if rep.temp_max is not None:
        lines.append(f"🌡 Температура: максимум {rep.temp_max:.0f}°C")
    if rep.disk_start is not None and rep.disk_end is not None:
        grow = rep.disk_end - rep.disk_start
        trend = f"вырос на {grow:.1f} п.п." if grow >= 0.5 else ("освободилось место" if grow <= -0.5 else "без изменений")
        lines.append(f"🗄 Диск /: {rep.disk_end:.0f}% ({trend})")
    if rep.busiest is not None:
        when = datetime.fromtimestamp(rep.busiest, tz).strftime("%d.%m %H:00")
        lines.append(f"⏱ Самый нагруженный час: {when}")
    if rep.coverage < 0.9:
        lines += ["", f"<i>Данные есть за {rep.coverage:.0%} периода: бот или сервер какое-то время не работал.</i>"]
    return "\n".join(lines)


def as_json(rep: Report | None) -> dict[str, Any] | None:
    return asdict(rep) if rep is not None else None
