"""Кино: «🍿 скачано» и «😕 раздач нет» — по одному сообщению на фильм/сериал.

* Фильм: как только в Radarr появился файл → сообщение с кнопкой «📺 Смотреть на ТВ».
* Сериал: появились новые серии → одно сообщение на сериал за проверку («новых серий — 3»).
* Добавленное через бота, для чего за 30 минут не нашлось раздачи, → «пока нет раздач» с кнопкой
  «искать ещё раз». Radarr/Sonarr при этом продолжают искать сами.

При первом запуске всё уже скачанное запоминается молча. Если доставка не удалась, событие
не помечается отправленным и уйдёт при следующей проверке.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from homebot.jobs.apps_watch import _loop
from homebot.services.quiet import Importance, set_importance
from homebot.services.radarr import RadarrService
from homebot.services.sonarr import SonarrService
from homebot.services.state import StateStore
from homebot.ui.movie_texts import render_movie_not_found, render_movie_ready, render_show_new_episodes, render_show_not_found

log = logging.getLogger(__name__)

KEY_REQUESTED = "movies.requested"  # {"m:<radarr_id>" | "s:<sonarr_id>": когда добавили через бота}
KEY_FILMS = "movies.with_file"  # radarr id фильмов, о которых уже сообщили / что были при старте
KEY_SHOWS = "movies.episodes"  # {sonarr id: сколько серий скачано}
NOT_FOUND_AFTER = 30 * 60


class MoviesWatchJob:
    def __init__(
        self,
        *,
        bot: Bot,
        admin_id: int,
        store: StateStore,
        radarr: RadarrService | None,
        sonarr: SonarrService | None,
        interval: int = 120,
        ready_markup: Callable[..., Any] | None = None,
        retry_markup: Callable[..., Any] | None = None,
    ) -> None:
        if ready_markup is None or retry_markup is None:
            from homebot.ui.keyboards import movie_ready_kb, movie_retry_kb  # aiogram; в тестах подставляются свои

            ready_markup, retry_markup = movie_ready_kb, movie_retry_kb
        self._bot = bot
        self._admin_id = admin_id
        self._store = store
        self._radarr = radarr
        self._sonarr = sonarr
        self._interval = interval
        self._ready = ready_markup
        self._retry = retry_markup

    async def run(self) -> None:
        set_importance(Importance.NORMAL)  # 🌙 ночной режим: см. services/quiet.py
        await _loop("Movies", self._interval, self.tick)

    async def _send(self, text: str, markup: Any) -> bool:
        try:
            await self._bot.send_message(self._admin_id, text, reply_markup=markup)
        except TelegramAPIError as exc:
            log.warning("Cannot deliver movie notification: %s", exc)
            return False
        return True

    async def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        present: set[str] = set()  # что скачано или качается — «раздачу нашли»
        if self._radarr is not None:
            present |= await self._films()
        if self._sonarr is not None:
            present |= await self._shows()
        await self._requests(present, now)

    async def _films(self) -> set[str]:
        assert self._radarr is not None
        films = await self._radarr.library()
        queue = await self._radarr.queue()
        with_file = {f.radarr_id: f for f in films if f.has_file}
        stored = self._store.get(KEY_FILMS)
        if stored is None:
            await self._store.set(KEY_FILMS, sorted(with_file))
        else:
            known = set(stored)
            delivered: set[int] = set()
            for fid, film in with_file.items():
                if fid not in known and await self._send(render_movie_ready(film), self._ready(film.tmdb_id)):
                    delivered.add(fid)
            updated = (known & set(with_file)) | delivered
            if updated != known:
                await self._store.set(KEY_FILMS, sorted(updated))
        return {f"m:{fid}" for fid in (*with_file, *queue)}

    async def _shows(self) -> set[str]:
        assert self._sonarr is not None
        shows = await self._sonarr.library()
        queue = await self._sonarr.queue()
        counts = {str(s.sonarr_id): s.episodes_have for s in shows}
        stored = self._store.get(KEY_SHOWS)
        if stored is None:
            await self._store.set(KEY_SHOWS, counts)
        else:
            known: dict[str, int] = dict(stored)
            updated = dict(known)
            for show in shows:
                key = str(show.sonarr_id)
                before = int(known.get(key, 0))
                if show.episodes_have > before:
                    text = render_show_new_episodes(show, show.episodes_have - before)
                    if not await self._send(text, self._ready(show.tvdb_id, series=True)):
                        continue
                updated[key] = show.episodes_have
            updated = {k: v for k, v in updated.items() if k in counts}  # удалённые сериалы забываем
            if updated != known:
                await self._store.set(KEY_SHOWS, updated)
        return {f"s:{s.sonarr_id}" for s in shows if s.has_file} | {f"s:{sid}" for sid in queue}

    async def _requests(self, present: set[str], now: float) -> None:
        requested: dict[str, float] = dict(self._store.get(KEY_REQUESTED) or {})
        if not requested:
            return
        remaining = dict(requested)
        for key, added in requested.items():
            if key in present:
                del remaining[key]  # нашлось — дальше сообщит «скачано»
                continue
            if now - float(added) < NOT_FOUND_AFTER:
                continue
            if await self._notify_not_found(key):
                del remaining[key]
        if remaining != requested:
            await self._store.set(KEY_REQUESTED, remaining)

    async def _notify_not_found(self, key: str) -> bool:
        kind, _, raw_id = key.partition(":")
        if not raw_id.isdigit():
            return True  # мусор — просто забываем
        item_id = int(raw_id)
        if kind == "m" and self._radarr is not None:
            film = next((f for f in await self._radarr.library() if f.radarr_id == item_id), None)
            if film is None:
                return True  # фильм удалили из Radarr
            return await self._send(render_movie_not_found(film), self._retry(film.tmdb_id))
        if kind == "s" and self._sonarr is not None:
            show = next((s for s in await self._sonarr.library() if s.sonarr_id == item_id), None)
            if show is None:
                return True
            return await self._send(render_show_not_found(show), self._retry(show.tvdb_id, series=True))
        return True
