"""Ежедневный бэкап. Если бот был выключен в нужный час — делает его при следующем запуске."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, tzinfo
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from homebot.services import backup as svc
from homebot.services.db import Database
from homebot.services.state import StateStore

log = logging.getLogger(__name__)


class BackupJob:
    def __init__(
        self,
        *,
        bot: Bot,
        admin_id: int,
        db: Database,
        store: StateStore,
        state_dir: Path,
        hour: int,
        keep: int,
        extra: list[Path],
        tz: tzinfo | None,
        interval: float = 600.0,
    ) -> None:
        self._bot = bot
        self._admin_id = admin_id
        self._db = db
        self._store = store
        self._state_dir = state_dir
        self._hour = hour
        self._keep = keep
        self._extra = extra
        self._tz = tz
        self._interval = interval

    def due(self, now: datetime) -> bool:
        scheduled = now.replace(hour=self._hour, minute=0, second=0, microsecond=0)
        last = int(svc.last_status(self._store).get("ts", 0) or 0)
        return now >= scheduled and last < scheduled.timestamp()

    async def run(self) -> None:
        log.info("Backup job running (daily at %02d:00)", self._hour)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Backup check failed")
            await asyncio.sleep(self._interval)

    async def tick(self) -> bool:
        if not self.due(datetime.now(self._tz)):
            return False
        await self.make()
        return True

    async def make(self) -> svc.BackupFile | None:
        try:
            return await svc.run_backup(self._state_dir, self._db, self._store, keep=self._keep, extra=self._extra, tz=self._tz)
        except svc.BackupError as exc:
            try:
                await self._bot.send_message(self._admin_id, f"💾 <b>Бэкап не получился</b>\n{exc}")
            except TelegramAPIError as send_exc:
                log.warning("Cannot report backup failure: %s", send_exc)
            return None
