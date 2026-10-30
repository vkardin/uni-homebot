"""Расчёт времени до ближайшего запуска (без зависимостей от aiogram)."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, tzinfo


def seconds_until(at: time, tz: tzinfo, *, now: datetime | None = None) -> float:
    """Сколько секунд до ближайшего `at` по часам `tz` (строго в будущем).

    Разность считается в UTC, поэтому переход на летнее/зимнее время не сбивает расчёт.
    """
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    target = current.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return (target.astimezone(UTC) - current.astimezone(UTC)).total_seconds()
