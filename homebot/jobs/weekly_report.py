"""📈 Отчёт за неделю: по понедельникам в 10:00 по часовому поясу UNI, один раз за неделю."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, tzinfo
from typing import Any

from homebot.services import reports

log = logging.getLogger(__name__)
KEY_SENT = "reports.weekly_sent"
SEND_HOUR = 10


def due(now: datetime, sent: str) -> str | None:
    """Номер недели, если отчёт пора отправить, иначе None."""
    week = now.strftime("%G-W%V")
    if now.weekday() != 0 or now.hour < SEND_HOUR or sent == week:
        return None
    return week


class WeeklyReportJob:
    def __init__(self, *, bot: Any, admin_id: int, db: Any, store: Any, tz: tzinfo) -> None:
        self._bot, self._admin, self._db, self._store, self._tz = bot, admin_id, db, store, tz

    async def tick(self) -> bool:
        week = due(datetime.now(self._tz), str(self._store.get(KEY_SENT, "")))
        if week is None:
            return False
        rep = await reports.make(self._db, "week")
        await self._store.set(KEY_SENT, week)  # даже без данных: не пытаться каждые 10 минут
        if rep is None:
            return False
        await self._bot.send_message(self._admin, reports.render(rep, "week", self._tz) + "\n\nМесяц и год: /report")
        return True

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Weekly report failed")
            await asyncio.sleep(600)
