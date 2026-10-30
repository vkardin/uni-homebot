"""Ежечасная сводка по расписанию, выровненному по часам: 9:00, 10:00, … (или :00/:30 при 30 минутах).

* Окно отбора новостей — с прошлой проверки, поэтому новости не теряются и не повторяются.
* В тихие часы обновлений нет; первая утренняя сводка — новое сообщение с главным за ночь.
* Настройки (частота, количество, режим, модель) читаются на каждом шаге — изменения в боте
  действуют сразу.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from aiogram import Bot

from homebot.services.brief import KEY_LAST_RUN, MAX_WINDOW, BriefService
from homebot.services.llm_queue import Priority, set_priority
from homebot.services.prefs import Prefs
from homebot.services.quiet import Importance, set_importance
from homebot.ui.texts import render_brief

log = logging.getLogger(__name__)

__all__ = ["KEY_LAST_RUN", "MAX_WINDOW", "HourlyNewsJob", "next_slot"]


def next_slot(now: datetime, interval_min: int, tz: tzinfo) -> datetime:
    """Ближайшее «круглое» время после now: для 60 мин — следующий :00, для 30 — :00/:30, для 120 — чётный час."""
    local = now.astimezone(tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes = (local - midnight).total_seconds() / 60
    slots = int(minutes // interval_min) + 1
    target = midnight + timedelta(minutes=slots * interval_min)
    now_utc = now.astimezone(UTC)
    while target.astimezone(UTC) <= now_utc:
        slots += 1
        target = midnight + timedelta(minutes=slots * interval_min)
    return target.astimezone(UTC)


class HourlyNewsJob:
    def __init__(
        self,
        *,
        bot: Bot,
        admin_id: int,
        briefs: BriefService,
        prefs: Prefs,
        tz: tzinfo,
        markup: Callable[[], Any] | None = None,
    ) -> None:
        if markup is None:
            from homebot.ui.keyboards import brief_kb  # aiogram-клавиатура; в тестах подставляется своя

            markup = brief_kb
        self._markup = markup
        self._bot = bot
        self._admin_id = admin_id
        self._briefs = briefs
        self._prefs = prefs
        self._tz = tz

    async def run(self) -> None:
        set_importance(Importance.NORMAL)  # 🌙 ночной режим: см. services/quiet.py
        set_priority(Priority.BACKGROUND)  # очередь к модели: см. services/llm_queue.py
        log.info("Hourly brief job running")
        while True:
            prefs = self._prefs.news()
            now = datetime.now(UTC)
            wait = (next_slot(now, prefs.interval_min, self._tz) - now).total_seconds()
            await asyncio.sleep(max(1.0, wait) + 5)  # +5 с: ленты часто обновляются ровно в :00
            try:
                await self.tick(datetime.now(UTC))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Hourly brief failed")

    async def tick(self, now: datetime) -> bool:
        """True — сводка отправлена или обновлена."""
        prefs = self._prefs.news()
        if not prefs.enabled:
            return False
        if prefs.is_quiet(now.astimezone(self._tz).hour):
            return False  # время прошлой проверки не трогаем: утром окно охватит ночь
        brief = await self._briefs.build(now, next_at=next_slot(now, prefs.interval_min, self._tz))
        return await self._briefs.deliver(self._bot, self._admin_id, render_brief(brief, self._tz), self._markup(), brief, prefs=prefs)
