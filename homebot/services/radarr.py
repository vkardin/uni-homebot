"""Radarr: поиск фильмов, добавление на закачку, очередь загрузок, популярное.

API v3, ключ — Radarr → Settings → General → API Key (deploy/update.sh подставляет его сам).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import aiohttp

from homebot.services.apps_http import AppError, request


@dataclass(frozen=True, slots=True)
class Film:
    tmdb_id: int
    title: str
    original_title: str = ""
    year: int | None = None
    overview: str = ""
    runtime: int = 0  # минуты
    genres: tuple[str, ...] = ()
    rating: float | None = None
    poster: str = ""
    radarr_id: int = 0  # 0 — фильма нет в Radarr
    has_file: bool = False
    status: str = ""  # announced / inCinemas / released
    size: int = 0  # сколько занимает на диске, байт

    @property
    def key(self) -> int:
        return self.tmdb_id

    @property
    def in_radarr(self) -> bool:
        return self.radarr_id > 0

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title


@dataclass(frozen=True, slots=True)
class Download:
    movie_id: int
    title: str
    size: int
    progress: float  # 0..100
    timeleft: str  # уже по-русски: «12 мин», «1 ч 5 мин»; пусто — неизвестно
    state: str  # downloading / importPending / importBlocked / failedPending …
    problem: str  # текст предупреждения Radarr, если есть


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _poster(item: dict[str, Any]) -> str:
    for image in item.get("images") or ():
        if isinstance(image, dict) and image.get("coverType") == "poster":
            url = image.get("remoteUrl") or image.get("url") or ""
            if isinstance(url, str) and url.startswith("http"):
                return url
    remote = item.get("remotePoster")
    return remote if isinstance(remote, str) and remote.startswith("http") else ""


def _rating(item: dict[str, Any]) -> float | None:
    ratings = item.get("ratings")
    if not isinstance(ratings, dict):
        return None
    for source in ("imdb", "tmdb"):
        entry = ratings.get(source)
        if isinstance(entry, dict) and isinstance(entry.get("value"), int | float) and entry["value"] > 0:
            return round(float(entry["value"]), 1)
    return None


def parse_film(item: Any) -> Film | None:
    if not isinstance(item, dict) or not _int(item.get("tmdbId")):
        return None
    year = _int(item.get("year")) or None
    return Film(
        tmdb_id=_int(item.get("tmdbId")),
        title=str(item.get("title") or item.get("originalTitle") or "?"),
        original_title=str(item.get("originalTitle") or ""),
        year=year,
        overview=str(item.get("overview") or "").strip(),
        runtime=_int(item.get("runtime")),
        genres=tuple(str(g) for g in item.get("genres") or () if g)[:3],
        rating=_rating(item),
        poster=_poster(item),
        radarr_id=_int(item.get("id")),
        has_file=bool(item.get("hasFile")),
        status=str(item.get("status") or ""),
        size=_int(item.get("sizeOnDisk")),
    )


def parse_films(data: Any) -> list[Film]:
    if not isinstance(data, list):
        raise AppError("неожиданный ответ Radarr")
    films: list[Film] = []
    seen: set[int] = set()
    for item in data:
        film = parse_film(item)
        if film and film.tmdb_id not in seen:
            seen.add(film.tmdb_id)
            films.append(film)
    return films


_TIMELEFT_RE = re.compile(r"^(?:(\d+)\.)?(\d+):(\d+):(\d+)")


def human_timeleft(value: Any) -> str:
    """'01:05:30' / '1.02:00:00' (формат .NET TimeSpan) → «1 ч 5 мин» / «1 д 2 ч»."""
    match = _TIMELEFT_RE.match(value) if isinstance(value, str) else None
    if not match:
        return ""
    days, hours, minutes = int(match[1] or 0), int(match[2]), int(match[3])
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{max(minutes, 1)} мин"


def parse_queue(data: Any) -> dict[int, Download]:
    records = data.get("records") if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise AppError("неожиданный ответ Radarr")
    result: dict[int, Download] = {}
    for r in records:
        if not isinstance(r, dict) or not _int(r.get("movieId")):
            continue
        raw_size = r.get("size")
        size = int(raw_size) if isinstance(raw_size, int | float) else 0
        left = r.get("sizeleft")
        left = float(left) if isinstance(left, int | float) else float(size)
        progress = (1 - left / size) * 100 if size else 0.0
        messages = [m for s in r.get("statusMessages") or () if isinstance(s, dict) for m in s.get("messages") or ()]
        problem = str(r.get("errorMessage") or (messages[0] if messages else "") or "")
        movie_id = _int(r.get("movieId"))
        current = result.get(movie_id)
        if current is not None and current.progress >= progress:
            continue  # одна раздача на фильм — показываем самую продвинутую
        result[movie_id] = Download(
            movie_id=movie_id,
            title=str(r.get("title") or ""),
            size=size,
            progress=max(0.0, min(progress, 100.0)),
            timeleft=human_timeleft(r.get("timeleft")),
            state=str(r.get("trackedDownloadState") or r.get("status") or ""),
            problem=problem,
        )
    return result


def _pick_profile(profiles: Any, wanted: str) -> int:
    if not isinstance(profiles, list) or not profiles:
        raise AppError("в Radarr нет профилей качества")
    wanted = wanted.strip().lower()
    for p in profiles:
        if isinstance(p, dict) and wanted and str(p.get("name", "")).lower() == wanted:
            return _int(p.get("id"))
    for p in profiles:  # по умолчанию — 1080p, если есть: 4K-ремуксы по 60 ГБ сюрприз для диска
        if isinstance(p, dict) and "1080" in str(p.get("name", "")):
            return _int(p.get("id"))
    return _int(profiles[0].get("id")) if isinstance(profiles[0], dict) else 0


def _pick_root(folders: Any) -> str:
    if not isinstance(folders, list):
        raise AppError("неожиданный ответ Radarr")
    usable = [f for f in folders if isinstance(f, dict) and f.get("path") and f.get("accessible", True)]
    if not usable:
        raise AppError("в Radarr не задана корневая папка (Settings → Media Management)")
    return str(usable[0]["path"])


class RadarrService:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str, *, profile: str = "") -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._headers = {"X-Api-Key": api_key}
        self._profile = profile

    async def _get(self, path: str, params: dict[str, str] | None = None, *, timeout: float = 15) -> Any:  # noqa: ASYNC109
        return await request(
            self._session, "GET", f"{self._base}/api/v3/{path}", headers=self._headers, params=params, timeout=timeout
        )

    async def lookup(self, term: str) -> list[Film]:
        """Поиск по названию (через метаданные Radarr/TMDB). Фильмы из библиотеки приходят с radarr_id."""
        return parse_films(await self._get("movie/lookup", {"term": term}, timeout=25))

    async def library(self) -> list[Film]:
        return parse_films(await self._get("movie", timeout=30))

    async def by_tmdb(self, tmdb_id: int) -> Film | None:
        """Карточка фильма: из библиотеки (с hasFile), иначе из поиска."""
        own = parse_films(await self._get("movie", {"tmdbId": str(tmdb_id)}))
        if own:
            return own[0]
        return parse_film(await self._get("movie/lookup/tmdb", {"tmdbId": str(tmdb_id)}, timeout=25))

    async def queue(self) -> dict[int, Download]:
        return parse_queue(await self._get("queue", {"pageSize": "200", "includeUnknownMovieItems": "false"}))

    async def discover(self) -> list[Film]:
        """Популярное и рекомендации (вкладка Discover в Radarr), без того, что уже есть."""
        data = await self._get(
            "importlist/movie",
            {"includeRecommendations": "true", "includeTrending": "true", "includePopular": "true"},
            timeout=30,
        )
        if not isinstance(data, list):
            raise AppError("неожиданный ответ Radarr")
        fresh = [item for item in data if isinstance(item, dict) and not item.get("isExisting") and not item.get("isExcluded")]
        return parse_films(fresh)

    async def add(self, tmdb_id: int) -> Film:
        """Добавить фильм и сразу запустить поиск раздачи."""
        existing = await self.by_tmdb(tmdb_id)
        if existing is not None and existing.in_radarr:
            return existing
        raw = await self._get("movie/lookup/tmdb", {"tmdbId": str(tmdb_id)}, timeout=25)
        if not isinstance(raw, dict) or not raw.get("tmdbId"):
            raise AppError("фильм не найден в базе TMDB")
        profiles, folders = await self._get("qualityprofile"), await self._get("rootfolder")
        body = {
            **raw,
            "qualityProfileId": _pick_profile(profiles, self._profile),
            "rootFolderPath": _pick_root(folders),
            "monitored": True,
            "minimumAvailability": "released",
            "addOptions": {"searchForMovie": True, "monitor": "movieOnly"},
        }
        body.pop("id", None)
        created = await request(self._session, "POST", f"{self._base}/api/v3/movie", headers=self._headers, json_body=body, timeout=30)
        film = parse_film(created)
        if film is None:
            raise AppError("Radarr не подтвердил добавление")
        return film

    async def search(self, radarr_id: int) -> None:
        await request(
            self._session,
            "POST",
            f"{self._base}/api/v3/command",
            headers=self._headers,
            json_body={"name": "MoviesSearch", "movieIds": [radarr_id]},
        )

    async def delete(self, radarr_id: int) -> None:
        """Убрать фильм из Radarr вместе с файлами (в список исключений не заносим — можно скачать снова)."""
        await request(
            self._session,
            "DELETE",
            f"{self._base}/api/v3/movie/{radarr_id}",
            headers=self._headers,
            params={"deleteFiles": "true", "addImportListExclusion": "false"},
            timeout=60,
        )
