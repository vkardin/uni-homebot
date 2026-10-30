"""Ежечасная сводка UNI: погода и курс → новости кратко → состояние сервера. Одно сообщение на день.

Режим «одно сообщение на день» (по умолчанию): первая сводка дня приходит новым сообщением
(с уведомлением), дальше каждый час UNI тихо обновляет его на месте. Режим «новое каждый раз»
присылает отдельное сообщение — но только если появились новые новости.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from homebot.services.db import Database, SentNews
from homebot.services.digest import Digest, DigestService
from homebot.services.news import NewsService, Story
from homebot.services.prefs import MODE_DAY, NewsPrefs, Prefs
from homebot.services.state import StateStore

if TYPE_CHECKING:
    from aiogram.types import InlineKeyboardMarkup

log = logging.getLogger(__name__)

KEY_LAST_RUN = "news.last_run"
KEY_MESSAGE = "brief.message"  # {"id": message_id, "day": "YYYY-MM-DD"}
MAX_WINDOW = timedelta(hours=12)


@dataclass(slots=True)
class Brief:
    created: datetime
    report: Digest
    stories: list[Story] = field(default_factory=list)  # новые за этот час
    earlier: list[SentNews] = field(default_factory=list)  # отправленные раньше сегодня
    news_errors: list[str] = field(default_factory=list)
    next_at: datetime | None = None
    collected: bool = True  # False — сводка без сбора новостей (просмотр по кнопке)


def news_window(store: StateStore, now: datetime, interval_min: int) -> timedelta:
    """С прошлой проверки, но не меньше интервала и не больше 12 часов."""
    last = store.get(KEY_LAST_RUN)
    if isinstance(last, int | float):
        return min(max(now - datetime.fromtimestamp(last, UTC), timedelta(minutes=interval_min)), MAX_WINDOW)
    return timedelta(minutes=interval_min)


class BriefService:
    def __init__(
        self,
        *,
        digest: DigestService,
        news: NewsService,
        db: Database,
        prefs: Prefs,
        store: StateStore,
        tz: tzinfo,
    ) -> None:
        self._digest = digest
        self._news = news
        self._db = db
        self._prefs = prefs
        self._store = store
        self._tz = tz

    def _day(self, moment: datetime) -> str:
        return moment.astimezone(self._tz).strftime("%Y-%m-%d")

    def _day_start(self, moment: datetime) -> int:
        local = moment.astimezone(self._tz)
        return int(local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    async def build(self, now: datetime, *, collect: bool = True, next_at: datetime | None = None) -> Brief:
        prefs = self._prefs.news()
        stories: list[Story] = []
        errors: list[str] = []
        if collect and prefs.feeds:
            report, batch = await asyncio.gather(
                self._digest.build(),
                self._news.run(prefs, now=now, window=news_window(self._store, now, prefs.interval_min)),
            )
            stories, errors = batch.stories, batch.errors
            await self._store.set(KEY_LAST_RUN, int(now.timestamp()))
        else:
            report = await self._digest.build()
        # Новости этого часа ещё не помечены отправленными, поэтому сюда попадают только прошлые.
        earlier = await self._db.sent_news_since(self._day_start(now))
        return Brief(now, report, stories, earlier, errors, next_at, collect)

    async def deliver(
        self,
        bot: Bot,
        chat_id: int,
        text: str,
        markup: InlineKeyboardMarkup,
        brief: Brief,
        *,
        prefs: NewsPrefs,
        reuse_message_id: int | None = None,
    ) -> bool:
        """Отправить или обновить сводку. True — доставлено. Новости помечаются отправленными только после доставки."""
        today = self._day(brief.created)
        stored = self._store.get(KEY_MESSAGE)
        target: int | None = reuse_message_id
        if target is None and prefs.mode == MODE_DAY and isinstance(stored, dict) and stored.get("day") == today:
            target = stored.get("id") if isinstance(stored.get("id"), int) else None
        if target is None and prefs.mode != MODE_DAY and not brief.stories and reuse_message_id is None:
            return False  # режим «новое каждый раз»: без новых новостей не шумим

        delivered_id: int | None = None
        if target is not None:
            try:
                await bot.edit_message_text(text=text, chat_id=chat_id, message_id=target, reply_markup=markup)
                delivered_id = target
            except TelegramBadRequest as exc:
                if "not modified" in str(exc).lower():
                    delivered_id = target
                else:
                    log.info("Brief message %s cannot be edited (%s); sending a new one", target, exc)
            except TelegramAPIError as exc:
                log.warning("Cannot update brief: %s", exc)
                return False
        if delivered_id is None:
            try:
                sent = await bot.send_message(chat_id, text, reply_markup=markup)
            except TelegramAPIError as exc:
                log.warning("Cannot deliver brief: %s", exc)
                return False
            delivered_id = sent.message_id

        if prefs.mode == MODE_DAY or reuse_message_id is not None:
            await self._store.set(KEY_MESSAGE, {"id": delivered_id, "day": today})
        if brief.stories:
            await self._news.mark_sent(brief.stories)
        return True
