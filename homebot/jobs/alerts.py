"""Фоновый «сторож»: следит за порогами и пишет администратору при проблемах."""

from __future__ import annotations

import asyncio
import html
import logging
import math
import time
from collections.abc import Callable, Sequence
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from homebot.config import Settings
from homebot.services.quiet import Importance, set_importance
from homebot.services.system import SystemService
from homebot.services.units import UnitsService

log = logging.getLogger(__name__)


LLM_GRACE_SEC = 120.0  # загрузка в снимке усредняется за прошлый интервал — хвост после модели тоже не считаем


class AlertsJob:
    """Раз в `alert_interval_sec` проверяет CPU/RAM/диски/температуру/упавшие службы.

    * CPU считается проблемой только после `alert_cpu_consecutive` проверок подряд —
      кратковременные пики не шумят. Пока считает локальная модель (пересказ новостей, перевод, чат)
      и ещё `LLM_GRACE_SEC` после неё, высокая загрузка процессора ожидаема и тревогой не считается.
    * Повтор по тому же поводу — не чаще, чем раз в `alert_cooldown_sec`.
    * Когда проблема исчезает, её «кулдаун» сбрасывается: новая вспышка придёт сразу.
    * Если отправка не удалась (нет сети), повод не считается отправленным — повторим на следующем тике.
    """

    def __init__(
        self,
        *,
        bot: Bot,
        admin_id: int,
        settings: Settings,
        system: SystemService,
        units: UnitsService,
        markup: Callable[[Sequence[str]], Any] | None = None,
        llm_busy: Callable[[], bool] | None = None,
    ) -> None:
        self._bot = bot
        self._admin_id = admin_id
        self._s = settings
        self._system = system
        self._units = units
        self._markup = markup
        self._llm_busy = llm_busy
        self._llm_until = -math.inf
        self._cpu_streak = 0
        self._last_sent: dict[str, float] = {}

    async def run(self) -> None:
        set_importance(Importance.CRITICAL)  # 🌙 ночной режим: см. services/quiet.py
        log.info("Alerts job running (interval=%ss)", self._s.alert_interval_sec)
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Alerts check failed")
            await asyncio.sleep(self._s.alert_interval_sec)

    async def _tick(self) -> None:
        s = self._s
        snap = await self._system.snapshot()
        statuses = await self._units.status_all()

        if self._llm_busy is not None and self._llm_busy():
            self._llm_until = time.monotonic() + LLM_GRACE_SEC
        llm_working = time.monotonic() < self._llm_until
        self._cpu_streak = self._cpu_streak + 1 if (snap.cpu_percent >= s.alert_cpu_percent and not llm_working) else 0

        active: dict[str, str] = {}
        if self._cpu_streak >= s.alert_cpu_consecutive:
            active["cpu"] = f"⚠️ CPU: {snap.cpu_percent:.0f}% — выше {s.alert_cpu_percent:.0f}% уже {self._cpu_streak} проверок подряд"
        if snap.ram_percent >= s.alert_ram_percent:
            active["ram"] = f"⚠️ RAM: {snap.ram_percent:.0f}% (порог {s.alert_ram_percent:.0f}%)"
        for disk in snap.disks:
            if disk.percent >= s.alert_disk_percent:
                active[f"disk:{disk.mountpoint}"] = (
                    f"💾 Диск <code>{html.escape(disk.mountpoint)}</code>: {disk.percent:.0f}% (порог {s.alert_disk_percent:.0f}%)"
                )
        if snap.temperature is not None and snap.temperature.current >= s.alert_temp_c:
            active["temp"] = f"🔥 Температура: {snap.temperature.current:.0f} °C (порог {s.alert_temp_c:.0f} °C)"
        for status in statuses:
            if status.is_failed:
                active[f"svc:{status.name}"] = f"🔴 Служба <code>{html.escape(status.name)}</code> упала (состояние failed)"

        for key in [k for k in self._last_sent if k not in active]:
            del self._last_sent[key]  # проблема ушла — следующая вспышка уведомит сразу

        now = time.monotonic()
        due = {key: text for key, text in active.items() if now - self._last_sent.get(key, -math.inf) >= s.alert_cooldown_sec}
        if not due:
            return

        message = "🚨 <b>Внимание</b>\n" + "\n".join(due.values())
        failed_units = [k.removeprefix("svc:") for k in due if k.startswith("svc:")]
        reply_markup = self._markup(failed_units) if (self._markup and failed_units) else None
        if await self._notify(message, reply_markup):
            for key in due:
                self._last_sent[key] = now

    async def _notify(self, text: str, reply_markup: Any = None) -> bool:
        try:
            if reply_markup is None:
                await self._bot.send_message(self._admin_id, text)
            else:
                await self._bot.send_message(self._admin_id, text, reply_markup=reply_markup)
        except TelegramAPIError as exc:
            log.warning("Cannot deliver alert: %s", exc)
            return False
        return True
