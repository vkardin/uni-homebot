"""Чистка диска: что из скачанного уже просмотрено (по Jellyfin), сколько это занимает, когда кончится место.

Подбор — чистая функция без сети. Удаляет Radarr/Sonarr (вместе с файлами), поэтому медиатека, очереди
и «Кино» остаются в согласии друг с другом. Фильм можно скачать снова — в список исключений он не попадает.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass

from homebot.services.apps_http import AppError
from homebot.services.jellyfin import JellyfinService, LibraryMovie
from homebot.services.radarr import Film, RadarrService
from homebot.services.sonarr import Show, SonarrService

KIND_MOVIE = "m"
KIND_SHOW = "s"
MAX_SHOWN = 8


@dataclass(frozen=True, slots=True)
class Watched:
    """Просмотренный фильм или сериал, который можно удалить."""

    kind: str  # KIND_MOVIE | KIND_SHOW
    ref: int  # внутренний id в Radarr / Sonarr (его же принимает delete)
    title: str
    year: int | None
    size: int  # байт на диске
    watched_days: int | None  # сколько дней назад досмотрели; None — дата неизвестна

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title


def signature(items: Sequence[Watched]) -> str:
    """Короткий отпечаток списка: кнопка «удалить всё» срабатывает, только если список не изменился."""
    raw = ",".join(f"{i.kind}{i.ref}" for i in items)
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:8]


def pick_watched(
    jelly: Sequence[LibraryMovie],
    films: Sequence[Film],
    shows: Sequence[Show],
    *,
    min_days: int = 3,
    now: float | None = None,
) -> list[Watched]:
    """Просмотренное и лежащее на диске, самое большое первым.

    `min_days` — «срок давности»: только что досмотренное не предлагаем (вдруг покажете ещё кому-то).
    """
    moment = time.time() if now is None else now
    by_tmdb = {f.tmdb_id: f for f in films if f.in_radarr and f.size > 0}
    by_tvdb = {s.tvdb_id: s for s in shows if s.in_sonarr and s.size > 0}
    result: dict[tuple[str, int], Watched] = {}
    for item in jelly:
        if not item.played:
            continue
        days = int((moment - item.last_played) // 86400) if item.last_played else None
        if days is not None and days < min_days:
            continue
        if item.kind == "Series":
            show = by_tvdb.get(item.tvdb_id) if item.tvdb_id else None
            if show is not None:
                result[(KIND_SHOW, show.sonarr_id)] = Watched(KIND_SHOW, show.sonarr_id, show.title, show.year, show.size, days)
        else:
            film = by_tmdb.get(item.tmdb_id) if item.tmdb_id else None
            if film is not None:
                result[(KIND_MOVIE, film.radarr_id)] = Watched(KIND_MOVIE, film.radarr_id, film.title, film.year, film.size, days)
    return sorted(result.values(), key=lambda w: w.size, reverse=True)


async def find_watched(
    jellyfin: JellyfinService,
    radarr: RadarrService | None,
    sonarr: SonarrService | None,
    *,
    min_days: int = 3,
) -> list[Watched]:
    """Собирает данные трёх сервисов. Недоступный Radarr/Sonarr просто выпадает из списка."""
    jelly: list[LibraryMovie] = []
    films: list[Film] = []
    shows: list[Show] = []
    errors: list[str] = []
    if radarr is not None:
        try:
            jelly += await jellyfin.movies("Movie")
            films = await radarr.library()
        except AppError as exc:
            errors.append(str(exc))
    if sonarr is not None:
        try:
            jelly += await jellyfin.movies("Series")
            shows = await sonarr.library()
        except AppError as exc:
            errors.append(str(exc))
    if errors and not (films or shows):
        raise AppError("; ".join(errors))
    return pick_watched(jelly, films, shows, min_days=min_days)


async def delete_watched(
    radarr: RadarrService | None, sonarr: SonarrService | None, items: Sequence[Watched]
) -> tuple[int, int, list[str]]:
    """Удаляет по списку. Возвращает (сколько удалено, освобождено байт, ошибки)."""
    removed = freed = 0
    errors: list[str] = []
    for item in items:
        service = radarr if item.kind == KIND_MOVIE else sonarr
        if service is None:
            errors.append(f"{item.label}: сервис не подключён")
            continue
        try:
            await service.delete(item.ref)
        except AppError as exc:
            errors.append(f"{item.label}: {exc}")
            continue
        removed += 1
        freed += item.size
    return removed, freed, errors


# ----------------------------------------------------------------- прогноз заполнения диска
@dataclass(frozen=True, slots=True)
class DiskForecast:
    percent: float  # занято сейчас, %
    per_day: float  # прирост, процентных пунктов в сутки
    days_left: float  # через сколько суток будет 100 %


def forecast_full(
    points: Sequence[tuple[int, float]], *, min_span_sec: int = 36 * 3600, min_growth: float = 0.05
) -> DiskForecast | None:
    """Линейный тренд занятости диска по точкам (unix-время, %). None — данных мало или диск не заполняется.

    Берём наименьшие квадраты по всем точкам: разовые скачки (скачали фильм → удалили) сглаживаются.
    """
    clean = [(ts, pct) for ts, pct in points if 0 <= pct <= 100]
    if len(clean) < 10 or clean[-1][0] - clean[0][0] < min_span_sec:
        return None
    t0 = clean[0][0]
    xs = [(ts - t0) / 86400 for ts, _ in clean]
    ys = [pct for _, pct in clean]
    n = len(clean)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    var = sum((x - mean_x) ** 2 for x in xs)
    if var == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / var
    if slope < min_growth:
        return None
    current = sum(ys[-5:]) / len(ys[-5:])  # сегодняшнее значение, а не точка прямой
    if current >= 100:
        return DiskForecast(current, slope, 0.0)
    return DiskForecast(current, slope, (100 - current) / slope)
