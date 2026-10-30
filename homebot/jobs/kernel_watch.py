"""🧯 Журнал ядра: сообщает об ошибках диска, памяти и перегреве, как только они появились."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from homebot.services.kernel_log import classify, read_kernel_log
from homebot.services.quiet import Importance, set_importance
from homebot.ui.extra_texts import render_kernel_problems

log = logging.getLogger(__name__)

KEY_LAST = "kernel.last_check"
KEY_SENT = "kernel.sent"
COOLDOWN = 6 * 3600  # об одной и той же категории — не чаще, чем раз в 6 часов
FIRST_LOOKBACK = 600  # при самом первом запуске смотрим на 10 минут назад, а не на всю историю


class KernelWatchJob:
    def __init__(
        self,
        *,
        bot: Bot,
        admin_id: int,
        store: Any,
        interval: int = 300,
        read: Callable[[int], Awaitable[tuple[list[str], str | None]]] = read_kernel_log,
    ) -> None:
        self._bot = bot
        self._admin_id = admin_id
        self._store = store
        self._interval = interval
        self._read = read
        self._warned_access = False

    async def run(self) -> None:
        set_importance(Importance.CRITICAL)  # 🌙 ночной режим: см. services/quiet.py
        log.info("Kernel log watch running (every %s s)", self._interval)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Kernel log watch failed")
            await asyncio.sleep(self._interval)

    async def tick(self, now: float | None = None) -> int:
        """Сколько категорий проблем отправлено."""
        moment = int(now if now is not None else time.time())
        since = int(self._store.get(KEY_LAST) or moment - FIRST_LOOKBACK)
        lines, error = await self._read(since)
        if error:
            if not self._warned_access:  # один раз в журнал, а не в чат: без доступа функция просто молчит
                log.warning("Kernel log is unavailable: %s", error)
                self._warned_access = True
            return 0
        problems = classify(lines)
        last_sent: dict[str, float] = dict(self._store.get(KEY_SENT) or {})
        fresh = [p for p in problems if p.key not in last_sent or moment - float(last_sent[p.key]) >= COOLDOWN]
        if fresh:
            try:
                await self._bot.send_message(self._admin_id, render_kernel_problems(fresh))
            except TelegramAPIError as exc:
                log.warning("Cannot deliver kernel log alert: %s", exc)
                return 0  # время проверки не сдвигаем: строки прочитаем снова
            await self._store.set(KEY_SENT, {**last_sent, **{p.key: float(moment) for p in fresh}})
        await self._store.set(KEY_LAST, moment)
        return len(fresh)
