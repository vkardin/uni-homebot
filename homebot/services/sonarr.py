"""Sonarr: сериалы — поиск, добавление (все сезоны или последний), очередь загрузок.

API v3 (Sonarr v3 и v4), ключ — Sonarr → Settings → General → API Key (update.sh подставляет сам).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp

from homebot.services.apps_http import AppError, request
from homebot.services.radarr import _int, _pick_profile, _pick_root, _poster, human_timeleft

MONITOR_ALL = "all"
MONITOR_LATEST = "latestSeason"


@dataclass(frozen=True, slots=True)
class Show:
    tvdb_id: int
    title: str
    year: int | None = None
    overview: str = ""
    runtime: int = 0  # длительность серии, минуты
    genres: tuple[str, ...] = ()
    rating: float | None = None
    poster: str = ""
    sonarr_id: int = 0  # 0 — сериала нет в Sonarr
    status: str = ""  # continuing / ended / upcoming
    network: str = ""
    seasons: int = 0  # без «спецвыпусков» (сезон 0)
    episodes_have: int = 0
    episodes_total: int = 0  # из отслеживаемых
    size: int = 0  # сколько занимает на диске, байт

    @property
    def key(self) -> int:
        return self.tvdb_id

    @property
    def in_sonarr(self) -> bool:
        return self.sonarr_id > 0

    @property
    def has_file(self) -> bool:
        return self.episodes_have > 0

    @property
    def complete(self) -> bool:
        return self.episodes_total > 0 and self.episodes_have >= self.episodes_total

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title


@dataclass(frozen=True, slots=True)
class ShowDownload:
    series_id: int
    episodes: int  # сколько серий в очереди
    progress: float  # средний прогресс, 0..100
    timeleft: str
    problem: str


def _rating(item: dict[str, Any]) -> float | None:
    ratings = item.get("ratings")
    value = ratings.get("value") if isinstance(ratings, dict) else None
    return round(float(value), 1) if isinstance(value, int | float) and value > 0 else None


def parse_show(item: Any) -> Show | None:
    if not isinstance(item, dict) or not _int(item.get("tvdbId")):
        return None
    stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    seasons = [s for s in item.get("seasons") or () if isinstance(s, dict) and _int(s.get("seasonNumber")) > 0]
    return Show(
        tvdb_id=_int(item.get("tvdbId")),
        title=str(item.get("title") or "?"),
        year=_int(item.get("year")) or None,
        overview=str(item.get("overview") or "").strip(),
        runtime=_int(item.get("runtime")),
        genres=tuple(str(g) for g in item.get("genres") or () if g)[:3],
        rating=_rating(item),
        poster=_poster(item),
        sonarr_id=_int(item.get("id")),
        status=str(item.get("status") or ""),
        network=str(item.get("network") or ""),
        seasons=len(seasons) or _int(stats.get("seasonCount")),
        episodes_have=_int(stats.get("episodeFileCount")),
        episodes_total=_int(stats.get("episodeCount")),
        size=_int(stats.get("sizeOnDisk")),
    )


def parse_shows(data: Any) -> list[Show]:
    if not isinstance(data, list):
        raise AppError("неожиданный ответ Sonarr")
    shows: list[Show] = []
    seen: set[int] = set()
    for item in data:
        show = parse_show(item)
        if show and show.tvdb_id not in seen:
            seen.add(show.tvdb_id)
            shows.append(show)
    return shows


def parse_show_queue(data: Any) -> dict[int, ShowDownload]:
    records = data.get("records") if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise AppError("неожиданный ответ Sonarr")
    grouped: dict[int, list[dict[str, Any]]] = {}
    for r in records:
        if isinstance(r, dict) and _int(r.get("seriesId")):
            grouped.setdefault(_int(r["seriesId"]), []).append(r)
    result: dict[int, ShowDownload] = {}
    for series_id, items in grouped.items():
        progresses, problem, timeleft = [], "", ""
        for r in items:
            size = r.get("size")
            left = r.get("sizeleft")
            if isinstance(size, int | float) and size > 0 and isinstance(left, int | float):
                progresses.append(max(0.0, min((1 - left / size) * 100, 100.0)))
            messages = [m for s in r.get("statusMessages") or () if isinstance(s, dict) for m in s.get("messages") or ()]
            problem = problem or str(r.get("errorMessage") or (messages[0] if messages else "") or "")
            timeleft = timeleft or human_timeleft(r.get("timeleft"))
        result[series_id] = ShowDownload(
            series_id=series_id,
            episodes=len(items),
            progress=sum(progresses) / len(progresses) if progresses else 0.0,
            timeleft=timeleft,
            problem=problem,
        )
    return result


class SonarrService:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str, *, profile: str = "") -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._headers = {"X-Api-Key": api_key}
        self._profile = profile

    async def _get(self, path: str, params: dict[str, str] | None = None, *, timeout: float = 15) -> Any:  # noqa: ASYNC109
        return await request(
            self._session, "GET", f"{self._base}/api/v3/{path}", headers=self._headers, params=params, timeout=timeout
        )

    async def lookup(self, term: str) -> list[Show]:
        return parse_shows(await self._get("series/lookup", {"term": term}, timeout=25))

    async def library(self) -> list[Show]:
        return parse_shows(await self._get("series", timeout=30))

    async def by_tvdb(self, tvdb_id: int) -> Show | None:
        own = parse_shows(await self._get("series", {"tvdbId": str(tvdb_id)}))
        if own:
            return own[0]
        found = parse_shows(await self._get("series/lookup", {"term": f"tvdb:{tvdb_id}"}, timeout=25))
        return found[0] if found else None

    async def queue(self) -> dict[int, ShowDownload]:
        return parse_show_queue(await self._get("queue", {"pageSize": "500", "includeUnknownSeriesItems": "false"}))

    async def add(self, tvdb_id: int, *, monitor: str = MONITOR_ALL) -> Show:
        """Добавить сериал и сразу искать серии: все сезоны или только последний."""
        if monitor not in (MONITOR_ALL, MONITOR_LATEST):
            raise AppError("неизвестный режим скачивания")
        existing = await self.by_tvdb(tvdb_id)
        if existing is not None and existing.in_sonarr:
            return existing
        found = await self._get("series/lookup", {"term": f"tvdb:{tvdb_id}"}, timeout=25)
        raw = found[0] if isinstance(found, list) and found and isinstance(found[0], dict) else None
        if raw is None:
            raise AppError("сериал не найден в базе TVDB")
        profiles, folders = await self._get("qualityprofile"), await self._get("rootfolder")
        body = {
            **raw,
            "qualityProfileId": _pick_profile(profiles, self._profile),
            "rootFolderPath": _pick_root(folders),
            "monitored": True,
            "seasonFolder": True,
            "seriesType": raw.get("seriesType") or "standard",
            "addOptions": {"monitor": monitor, "searchForMissingEpisodes": True, "searchForCutoffUnmetEpisodes": False},
        }
        try:  # Sonarr v3 требует языковой профиль; в v4 его нет — тогда просто пропускаем
            languages = await self._get("languageprofile")
            if isinstance(languages, list) and languages and isinstance(languages[0], dict):
                body["languageProfileId"] = _int(languages[0].get("id"))
        except AppError:
            pass
        body.pop("id", None)
        created = await request(
            self._session, "POST", f"{self._base}/api/v3/series", headers=self._headers, json_body=body, timeout=30
        )
        show = parse_show(created)
        if show is None:
            raise AppError("Sonarr не подтвердил добавление")
        return show

    async def search(self, sonarr_id: int) -> None:
        await request(
            self._session,
            "POST",
            f"{self._base}/api/v3/command",
            headers=self._headers,
            json_body={"name": "SeriesSearch", "seriesId": sonarr_id},
        )

    async def delete(self, sonarr_id: int) -> None:
        """Убрать сериал из Sonarr вместе с файлами (в список исключений не заносим — можно скачать снова)."""
        await request(
            self._session,
            "DELETE",
            f"{self._base}/api/v3/series/{sonarr_id}",
            headers=self._headers,
            params={"deleteFiles": "true", "addImportListExclusion": "false"},
            timeout=60,
        )
