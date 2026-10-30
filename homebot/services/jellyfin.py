"""Jellyfin: кто что смотрит, что недавно добавлено, фильмы библиотеки и пульт для телевизора.

API-ключ — Панель управления → API-ключи. Пульт работает с любым открытым клиентом Jellyfin,
который поддерживает удалённое управление (Android TV, веб, телефон).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiohttp

from homebot.services.apps_http import AppError, request

_TICKS_PER_SEC = 10_000_000


@dataclass(frozen=True, slots=True)
class NowPlaying:
    user: str
    device: str
    title: str
    progress: float | None  # 0..100
    paused: bool


@dataclass(frozen=True, slots=True)
class MediaItem:
    title: str
    kind: str
    year: int | None


def _title(item: dict[str, Any]) -> str:
    name = str(item.get("Name") or "?")
    series = item.get("SeriesName")
    if series:
        season = item.get("ParentIndexNumber")
        episode = item.get("IndexNumber")
        code = f" S{season:02d}E{episode:02d}" if isinstance(season, int) and isinstance(episode, int) else ""
        return f"{series}{code} — {name}"
    return name


def parse_sessions(data: Any) -> list[NowPlaying]:
    if not isinstance(data, list):
        raise AppError("неожиданный ответ Jellyfin")
    result = []
    for session in data:
        if not isinstance(session, dict) or not isinstance(session.get("NowPlayingItem"), dict):
            continue
        item = session["NowPlayingItem"]
        state = session.get("PlayState") or {}
        position = state.get("PositionTicks")
        runtime = item.get("RunTimeTicks")
        progress = position / runtime * 100 if isinstance(position, int) and isinstance(runtime, int) and runtime else None
        result.append(
            NowPlaying(
                user=str(session.get("UserName") or "?"),
                device=str(session.get("DeviceName") or session.get("Client") or ""),
                title=_title(item),
                progress=progress,
                paused=bool(state.get("IsPaused")),
            )
        )
    return result


def parse_items(data: Any) -> list[MediaItem]:
    items = data.get("Items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise AppError("неожиданный ответ Jellyfin")
    return [
        MediaItem(
            _title(i),
            str(i.get("Type") or ""),
            i.get("ProductionYear") if isinstance(i.get("ProductionYear"), int) else None,
        )
        for i in items
        if isinstance(i, dict)
    ]


class _JellyfinBase:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f'MediaBrowser Token="{api_key}"', "X-Emby-Token": api_key}
        self._user_id: str | None = None

    async def now_playing(self) -> list[NowPlaying]:
        return parse_sessions(await request(self._session, "GET", f"{self._base}/Sessions", headers=self._headers))

    async def recently_added(self, limit: int = 8) -> list[MediaItem]:
        data = await request(
            self._session,
            "GET",
            f"{self._base}/Items",
            headers=self._headers,
            params={
                "SortBy": "DateCreated",
                "SortOrder": "Descending",
                "IncludeItemTypes": "Movie,Episode",
                "Recursive": "true",
                "Limit": str(limit),
            },
        )
        return parse_items(data)


@dataclass(frozen=True, slots=True)
class LibraryMovie:
    id: str
    title: str
    year: int | None
    tmdb_id: int
    played: bool
    resume_ticks: int  # с какого места продолжить; 0 — с начала
    runtime_ticks: int
    tvdb_id: int = 0
    kind: str = "Movie"  # Movie / Series / Episode
    last_played: int = 0  # unix-время последнего просмотра; 0 — неизвестно

    @property
    def resume_percent(self) -> float:
        return self.resume_ticks / self.runtime_ticks * 100 if self.runtime_ticks else 0.0


@dataclass(frozen=True, slots=True)
class Screen:
    """Клиент Jellyfin, которым можно управлять (телевизор, браузер, телефон)."""

    id: str
    device: str
    client: str
    title: str  # что играет сейчас; пусто — ничего
    position_ticks: int
    runtime_ticks: int
    paused: bool
    volume: int | None

    @property
    def is_tv(self) -> bool:
        text = f"{self.device} {self.client}".lower()
        return any(word in text for word in ("tv", "stick", "android tv", "shield", "fire", "mi box", "chromecast"))

    @property
    def playing(self) -> bool:
        return bool(self.title)


def _iso_ts(raw: Any) -> int:
    if not isinstance(raw, str) or not raw:
        return 0
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return int((parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp())


def parse_library(data: Any) -> list[LibraryMovie]:
    items = data.get("Items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise AppError("неожиданный ответ Jellyfin")
    result = []
    for item in items:
        if not isinstance(item, dict) or not item.get("Id"):
            continue
        ids = item.get("ProviderIds") or {}
        ids = ids if isinstance(ids, dict) else {}
        raw_tmdb = ids.get("Tmdb") or ids.get("tmdb") or ""
        raw_tvdb = ids.get("Tvdb") or ids.get("tvdb") or ""
        user = item.get("UserData") or {}
        year = item.get("ProductionYear")
        runtime = item.get("RunTimeTicks")
        position = user.get("PlaybackPositionTicks") if isinstance(user, dict) else 0
        result.append(
            LibraryMovie(
                id=str(item["Id"]),
                title=_title(item),
                year=year if isinstance(year, int) else None,
                tmdb_id=int(raw_tmdb) if str(raw_tmdb).isdigit() else 0,
                played=bool(isinstance(user, dict) and user.get("Played")),
                resume_ticks=position if isinstance(position, int) and position > 0 else 0,
                runtime_ticks=runtime if isinstance(runtime, int) else 0,
                tvdb_id=int(raw_tvdb) if str(raw_tvdb).isdigit() else 0,
                kind=str(item.get("Type") or "Movie"),
                last_played=_iso_ts(user.get("LastPlayedDate")) if isinstance(user, dict) else 0,
            )
        )
    return result


def parse_screens(data: Any) -> list[Screen]:
    if not isinstance(data, list):
        raise AppError("неожиданный ответ Jellyfin")
    screens = []
    for s in data:
        if not isinstance(s, dict) or not s.get("Id") or not s.get("SupportsRemoteControl"):
            continue
        item = s.get("NowPlayingItem") if isinstance(s.get("NowPlayingItem"), dict) else {}
        state = s.get("PlayState") if isinstance(s.get("PlayState"), dict) else {}
        position = state.get("PositionTicks")
        runtime = item.get("RunTimeTicks")
        volume = state.get("VolumeLevel")
        screens.append(
            Screen(
                id=str(s["Id"]),
                device=str(s.get("DeviceName") or ""),
                client=str(s.get("Client") or ""),
                title=_title(item) if item else "",
                position_ticks=position if isinstance(position, int) else 0,
                runtime_ticks=runtime if isinstance(runtime, int) else 0,
                paused=bool(state.get("IsPaused")),
                volume=volume if isinstance(volume, int) else None,
            )
        )
    return screens


def pick_screen(screens: list[Screen], prefer: str = "") -> Screen | None:
    """Куда показывать: устройство из настроек → то, что уже играет → телевизор → любое."""
    if not screens:
        return None
    prefer = prefer.strip().lower()
    if prefer:
        named = [s for s in screens if prefer in s.device.lower() or prefer in s.client.lower()]
        if named:
            screens = named
    return sorted(screens, key=lambda s: (s.playing, s.is_tv), reverse=True)[0]


def find_by_tmdb(
    movies: list[LibraryMovie], tmdb_id: int, title: str = "", year: int | None = None, *, tvdb_id: int = 0
) -> LibraryMovie | None:
    for movie in movies:
        if (tmdb_id and movie.tmdb_id == tmdb_id) or (tvdb_id and movie.tvdb_id == tvdb_id):
            return movie
    if title:  # запасной вариант: метаданные Jellyfin ещё не подтянулись
        wanted = title.strip().lower()
        for movie in movies:
            if movie.title.strip().lower() == wanted and (year is None or movie.year in (None, year)):
                return movie
    return None


class JellyfinService(_JellyfinBase):
    """Плюс библиотека фильмов и пульт: что смотреть и на каком экране."""

    async def _req(self, method: str, path: str, params: dict[str, str] | None = None) -> Any:
        return await request(self._session, method, f"{self._base}{path}", headers=self._headers, params=params)

    async def user_id(self) -> str:
        if self._user_id is None:
            users = await self._req("GET", "/Users")
            if not isinstance(users, list) or not users:
                raise AppError("в Jellyfin нет пользователей")
            admins = [u for u in users if isinstance(u, dict) and (u.get("Policy") or {}).get("IsAdministrator")]
            chosen = (admins or users)[0]
            self._user_id = str(chosen.get("Id") or "")
        return self._user_id

    async def movies(self, kind: str = "Movie") -> list[LibraryMovie]:
        """Фильмы (kind=Movie) или сериалы (kind=Series) библиотеки с отметками «просмотрено»."""
        data = await self._req(
            "GET",
            "/Items",
            {
                "userId": await self.user_id(),
                "IncludeItemTypes": kind,
                "Recursive": "true",
                "Fields": "ProviderIds",
                "SortBy": "DateCreated",
                "SortOrder": "Descending",
                "Limit": "1000",
            },
        )
        return parse_library(data)

    async def next_episode(self, series_id: str) -> LibraryMovie | None:
        """Следующая непросмотренная серия (или та, что недосмотрена)."""
        user = await self.user_id()
        data = await self._req(
            "GET",
            "/Shows/NextUp",
            {"userId": user, "seriesId": series_id, "Limit": "1", "Fields": "ProviderIds", "enableResumable": "true"},
        )
        episodes = parse_library(data)
        if episodes:
            return episodes[0]
        data = await self._req("GET", f"/Shows/{series_id}/Episodes", {"userId": user, "Fields": "ProviderIds"})
        unseen = [e for e in parse_library(data) if not e.played]
        return unseen[0] if unseen else None

    async def screens(self) -> list[Screen]:
        data = await self._req("GET", "/Sessions", {"ControllableByUserId": await self.user_id(), "ActiveWithinSeconds": "3600"})
        return parse_screens(data)

    async def play(self, screen_id: str, item_id: str, start_ticks: int = 0) -> None:
        params = {"playCommand": "PlayNow", "itemIds": item_id}
        if start_ticks > 0:
            params["startPositionTicks"] = str(start_ticks)
        await self._req("POST", f"/Sessions/{screen_id}/Playing", params)

    async def refresh_library(self) -> None:
        await self._req("POST", "/Library/Refresh")
