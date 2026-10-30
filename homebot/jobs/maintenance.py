"""Обслуживание: здоровье дисков (SMART) и обновления безопасности. Пишет только когда что-то изменилось."""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from homebot.services.quiet import Importance, set_importance
from homebot.services.smart import SmartService, smart_issues
from homebot.services.state import StateStore
from homebot.services.updates import UpdatesService
from homebot.ui.texts import render_smart_alert, render_updates_alert

log = logging.getLogger(__name__)

SMART_EVERY = 1800
UPDATES_EVERY = 6 * 3600
_KEY_SMART = "maint.smart_signature"
_KEY_UPDATES = "maint.updates_signature"


class MaintenanceJob:
    def __init__(self, *, bot: Bot, admin_id: int, store: StateStore, smart: SmartService, updates: UpdatesService) -> None:
        self._bot = bot
        self._admin_id = admin_id
        self._store = store
        self._smart = smart
        self._updates = updates
        self._next_smart = 0.0
        self._next_updates = time.monotonic() + 120  # не дёргаем apt сразу при старте

    async def run(self) -> None:
        set_importance(Importance.CRITICAL)  # 🌙 ночной режим: см. services/quiet.py
        log.info("Maintenance job running")
        while True:
            now = time.monotonic()
            try:
                if now >= self._next_smart:
                    self._next_smart = now + SMART_EVERY
                    await self.check_smart()
                if now >= self._next_updates:
                    self._next_updates = now + UPDATES_EVERY
                    await self.check_updates()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Maintenance check failed")
            await asyncio.sleep(60)

    async def check_smart(self) -> None:
        report = self._smart.read()
        if report is None:
            return
        issues = sorted(smart_issues(report))
        if issues == (self._store.get(_KEY_SMART) or []):
            return
        if issues and await self._send(render_smart_alert(issues)):
            await self._store.set(_KEY_SMART, issues)
        elif not issues:
            await self._store.set(_KEY_SMART, [])  # всё починилось — следующая проблема придёт сразу

    async def check_updates(self) -> None:
        info = await self._updates.check()
        if info.error:
            log.warning("Updates check failed: %s", info.error)
            return
        signature = list(info.signature)
        if signature == self._store.get(_KEY_UPDATES):
            return
        important = info.security > 0 or info.reboot_required
        if not important:
            await self._store.set(_KEY_UPDATES, signature)
            return
        if await self._send(render_updates_alert(info)):
            await self._store.set(_KEY_UPDATES, signature)

    async def _send(self, text: str) -> bool:
        try:
            await self._bot.send_message(self._admin_id, text)
        except TelegramAPIError as exc:
            log.warning("Cannot deliver maintenance notification: %s", exc)
            return False
        return True
