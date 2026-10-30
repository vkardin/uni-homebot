"""Доставка напоминаний. Если Telegram недоступен, напоминание не теряется — уйдёт при следующей проверке."""

from __future__ import annotations

import asyncio
import html
import logging
import time
from datetime import datetime, tzinfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

from homebot.services.db import Database
from homebot.services.reminders import next_due

log = logging.getLogger(__name__)


class RemindersJob:
    def __init__(self, *, bot: Bot, admin_id: int, db: Database, interval: float = 15.0, tz: tzinfo | None = None) -> None:
        self._bot = bot
        self._admin_id = admin_id
        self._db = db
        self._interval = interval
        self._tz = tz

    async def run(self) -> None:
        log.info("Reminders job running")
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Reminders check failed")
            await asyncio.sleep(self._interval)

    async def tick(self) -> int:
        sent = 0
        for reminder in await self._db.due_reminders(int(time.time())):
            try:
                await self._bot.send_message(reminder.owner or self._admin_id, f"⏰ <b>Напоминание</b>\n{html.escape(reminder.text)}")
            except TelegramForbiddenError as exc:
                if not reminder.owner:  # свой чат недоступен — как обычный сбой
                    log.warning("Cannot deliver reminder %s: %s", reminder.id, exc)
                    break
                log.info("Reminder %s: chat %s closed the bot — dropping", reminder.id, reminder.owner)
                await self._db.mark_reminder_done(reminder.id)  # гость заблокировал бота — не держим очередь
                continue
            except TelegramAPIError as exc:
                log.warning("Cannot deliver reminder %s: %s", reminder.id, exc)
                break  # сеть недоступна — попробуем всё позже
            if reminder.repeat:
                now = datetime.now(self._tz)
                anchor = datetime.fromtimestamp(reminder.due_ts, self._tz)
                try:
                    await self._db.reschedule_reminder(reminder.id, int(next_due(reminder.repeat, now, anchor).timestamp()))
                except (ValueError, IndexError):
                    log.warning("Bad repeat rule %r of reminder %s; making it one-off", reminder.repeat, reminder.id)
                    await self._db.mark_reminder_done(reminder.id)
            else:
                await self._db.mark_reminder_done(reminder.id)
            sent += 1
        return sent
