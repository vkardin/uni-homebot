"""Анализ замеров скорости: «упала относительно обычной» и недельная статистика.

Порог из .env (SPEEDTEST_MIN_DOWN_MBPS) — это абсолют: «ниже 50 Мбит/с — плохо». Здесь — относительное:
у каждого свой тариф, и «упало вдвое от обычного» работает без настройки.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from homebot.services.speedtest import SpeedResult

RECENT = 3  # сколько последних замеров подряд должны быть плохими
MIN_BASELINE_SAMPLES = 8  # сколько замеров нужно для «обычной» скорости
RECOVERED_SHARE = 0.8  # скорость «вернулась», когда достигла 80 % обычной
MIN_BASELINE_MBPS = 5.0  # на совсем медленных линиях «упало вдвое» — это шум


@dataclass(frozen=True, slots=True)
class SpeedDrop:
    baseline: float  # обычная загрузка, Мбит/с (медиана)
    recent: float  # среднее последних замеров
    share: float  # recent / baseline, 0..1


def _downs(results: Sequence[SpeedResult]) -> list[float]:
    return [r.download_mbps for r in results if r.ok and r.download_mbps is not None]


def baseline_of(history: Sequence[SpeedResult], *, recent: int = RECENT) -> float | None:
    """Медиана загрузки по замерам, КРОМЕ последних `recent` (иначе просадка сама себя «нормализует»)."""
    ok = [r for r in history if r.ok and r.download_mbps is not None]
    older = _downs(ok[:-recent] if recent else ok)
    if len(older) < MIN_BASELINE_SAMPLES:
        return None
    value = statistics.median(older)
    return value if value >= MIN_BASELINE_MBPS else None


def assess(history: Sequence[SpeedResult], *, drop_percent: int, recent: int = RECENT) -> tuple[str, SpeedDrop | None]:
    """('degraded' | 'recovered' | 'unknown', детали).

    * degraded — ВСЕ последние `recent` замеров ниже (100 − drop_percent) % обычной скорости;
    * recovered — последний замер вернулся к 80 % обычной;
    * unknown — данных мало или скорость где-то посередине (состояние не меняем).
    """
    if drop_percent <= 0:
        return "unknown", None
    ok = [r for r in history if r.ok and r.download_mbps is not None]
    base = baseline_of(ok, recent=recent)
    if base is None or len(ok) < recent:
        return "unknown", None
    tail = _downs(ok[-recent:])
    average = sum(tail) / len(tail)
    detail = SpeedDrop(base, average, average / base)
    floor = base * (100 - drop_percent) / 100
    if all(value < floor for value in tail):
        return "degraded", detail
    if tail[-1] >= base * RECOVERED_SHARE:
        return "recovered", detail
    return "unknown", detail


@dataclass(frozen=True, slots=True)
class PeriodStats:
    count: int  # удачных замеров
    failed: int
    down_avg: float
    down_median: float
    down_min: float
    down_p10: float  # «в плохие часы»: 10 % замеров были медленнее этого
    up_avg: float
    ping_avg: float | None


def _percentile(sorted_values: Sequence[float], share: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, int(share * (len(sorted_values) - 1) + 0.5)))
    return sorted_values[index]


def period_stats(results: Sequence[SpeedResult]) -> PeriodStats | None:
    good = [r for r in results if r.ok and r.download_mbps is not None]
    if not good:
        return None
    downs = sorted(r.download_mbps or 0.0 for r in good)
    ups = [r.upload_mbps or 0.0 for r in good]
    pings = [r.ping_ms for r in good if r.ping_ms is not None]
    return PeriodStats(
        count=len(good),
        failed=len(results) - len(good),
        down_avg=sum(downs) / len(downs),
        down_median=statistics.median(downs),
        down_min=downs[0],
        down_p10=_percentile(downs, 0.1),
        up_avg=sum(ups) / len(ups),
        ping_avg=sum(pings) / len(pings) if pings else None,
    )


def split_weeks(history: Sequence[SpeedResult], now: datetime) -> tuple[PeriodStats | None, PeriodStats | None]:
    """(последние 7 суток, предыдущие 7 суток)."""
    edge = now - timedelta(days=7)
    start = now - timedelta(days=14)
    this = [r for r in history if r.created_at is not None and r.created_at >= edge]
    prev = [r for r in history if r.created_at is not None and start <= r.created_at < edge]
    return period_stats(this), period_stats(prev)
