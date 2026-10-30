"""🎁 Бесплатные игры: сообщает о новой раздаче один раз и напоминает в последние сутки."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from homebot.services.apps_http import AppError
from homebot.services.free_games import FreeGame
from homebot.services.quiet import Importance, set_importance
from homebot.ui.extra_texts import render_free_game

log = logging.getLogger(__name__)

MAX_REMEMBERED = 300
MAX_PER_TICK = 6
REMIND_BEFORE = timedelta(hours=24)


def _default_preview(url: str | None) -> Any:
    from homebot.utils.telegram import link_preview  # тянет aiogram.types; в тестах подставляется своя

    return link_preview(url)


class FreeGamesJob:
    KEY_SEEN = "games.seen"
    KEY_REMINDED = "games.reminded"

    def __init__(
        self,
        *,
        bot: Bot,
        admin_id: int,
        store: Any,
        fetch: Callable[[], Awaitable[list[FreeGame]]],
        tz: tzinfo,
        interval: int = 2 * 3600,
        preview: Callable[[str | None], Any] = _default_preview,
        is_night: Callable[[], bool] | None = None,
    ) -> None:
        self._bot = bot
        self._admin_id = admin_id
        self._store = store
        self._fetch = fetch
        self._tz = tz
        self._interval = interval
        self._preview = preview
        self._is_night = is_night  # раздачи длятся дни: ночью не будим, сообщим утром

    async def run(self) -> None:
        set_importance(Importance.NORMAL)  # 🌙 ночной режим: см. services/quiet.py
        log.info("Free games watch running (every %s s)", self._interval)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except AppError as exc:
                log.warning("Free games: %s", exc)
            except Exception:
                log.exception("Free games watch failed")
            await asyncio.sleep(self._interval)

    async def _send(self, game: FreeGame, text: str) -> bool:
        try:
            await self._bot.send_message(self._admin_id, text, link_preview_options=self._preview(game.image or None))
        except TelegramAPIError as exc:
            log.warning("Cannot deliver free game notification: %s", exc)
            return False
        return True

    async def tick(self, now: datetime | None = None) -> int:
        """Сколько сообщений отправлено."""
        if self._is_night is not None and self._is_night():
            return 0
        moment = now or datetime.now(UTC)
        games = [g for g in await self._fetch() if g.is_active(moment)]
        seen: list[str] = list(self._store.get(self.KEY_SEEN) or [])
        reminded: list[str] = list(self._store.get(self.KEY_REMINDED) or [])
        announced_before = set(seen)
        sent = 0

        for game in [g for g in games if g.key not in announced_before][:MAX_PER_TICK]:
            if not await self._send(game, render_free_game(game, self._tz, moment)):
                continue
            seen.append(game.key)
            sent += 1
            if game.end is not None and game.end - moment <= REMIND_BEFORE:
                reminded.append(game.key)  # объявили уже в последние сутки — напоминать второй раз незачем

        for game in games:
            if game.key not in announced_before or game.key in reminded or game.end is None:
                continue
            if game.end - moment <= REMIND_BEFORE and await self._send(game, render_free_game(game, self._tz, moment, last_day=True)):
                reminded.append(game.key)
                sent += 1

        await self._store.set(self.KEY_SEEN, seen[-MAX_REMEMBERED:])
        await self._store.set(self.KEY_REMINDED, reminded[-MAX_REMEMBERED:])
        return sent
