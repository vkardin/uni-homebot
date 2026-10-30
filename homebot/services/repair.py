"""🛠 Ремонт сервера: перезапуск бота, служб, упавших задач и (по желанию) перезагрузка — по PIN и с журналом.

Безопасность:
* модуль выключен, пока в .env на сервере нет REPAIR_ENABLED=true и REPAIR_PIN (из чата и панели не включить);
* только действия из белого списка ниже; службы — только из RESTART_SERVICES, перезагрузка — только при POWER_ENABLED;
* PIN спрашивается на каждое действие; после MAX_FAILS неверных PIN подряд модуль закрывается на LOCK_SEC;
* каждое действие и каждая неверная попытка пишутся в STATE_DIR/repair.log (права 600).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import signal
import time
from dataclasses import dataclass
from typing import Any

from homebot.config import Settings
from homebot.services.power import PowerService
from homebot.services.tasks import TaskManager

log = logging.getLogger(__name__)

LOG_NAME = "repair.log"
MAX_FAILS = 5
LOCK_SEC = 15 * 60
PIN_RE = re.compile(r"\d{4,12}")


@dataclass(frozen=True, slots=True)
class Action:
    id: str  # "bot", "tasks", "reboot", "svc:<unit>"
    title: str
    danger: bool = False


def enabled(settings: Settings) -> bool:
    pin = settings.repair_pin.get_secret_value() if settings.repair_pin else ""
    return settings.repair_enabled and bool(PIN_RE.fullmatch(pin))


class Repair:
    def __init__(self, settings: Settings, power: PowerService, tasks: TaskManager) -> None:
        self._settings = settings
        self._power = power
        self._tasks = tasks
        self._log = settings.state_dir / LOG_NAME
        self._fails = 0
        self._locked_until = 0.0

    @property
    def enabled(self) -> bool:
        return enabled(self._settings)

    def actions(self) -> list[Action]:
        out = [Action("bot", "🔄 Перезапустить бота"), Action("tasks", "🩹 Перезапустить упавшие задачи")]
        out += [Action(f"svc:{unit}", f"♻️ Перезапустить {unit}") for unit in self._power.restartable]
        if self._power.power_enabled:
            out.append(Action("reboot", "⚠️ Перезагрузить сервер", danger=True))
        return out

    def locked(self, now: float | None = None) -> int:
        """Сколько секунд ещё закрыт после перебора PIN (0 — открыт)."""
        return max(0, int(self._locked_until - (now if now is not None else time.time())))

    def check_pin(self, pin: str, user: int, now: float | None = None) -> str:
        """'ok', 'wrong', 'locked' или 'off'."""
        now = now if now is not None else time.time()
        if not self.enabled:
            return "off"
        if self.locked(now):
            return "locked"
        expected = self._settings.repair_pin.get_secret_value()  # type: ignore[union-attr]
        if secrets.compare_digest(pin.strip().encode(), expected.encode()):
            self._fails = 0
            return "ok"
        self._fails += 1
        self.journal(user, "pin", False, f"неверный PIN ({self._fails}/{MAX_FAILS})", now=now)
        if self._fails >= MAX_FAILS:
            self._fails, self._locked_until = 0, now + LOCK_SEC
            return "locked"
        return "wrong"

    async def run(self, action_id: str, pin: str, user: int) -> tuple[bool, str]:
        status = self.check_pin(pin, user)
        if status != "ok":
            return False, {"off": "модуль выключен", "locked": f"закрыто на {self.locked() // 60 + 1} мин после неверных PIN",
                           "wrong": "неверный PIN"}[status]  # fmt: skip
        if action_id not in {a.id for a in self.actions()}:
            return False, "такого действия нет"
        ok, detail = await self._do(action_id)
        self.journal(user, action_id, ok, detail)
        return ok, detail

    async def _do(self, action_id: str) -> tuple[bool, str]:
        if action_id == "bot":
            # SIGTERM через секунду (ответ успеет уйти): aiogram штатно останавливается, systemd (Restart=always) поднимает бота
            pid = os.getpid()
            asyncio.get_running_loop().call_later(1.0, os.kill, pid, signal.SIGTERM)
            return True, "бот перезапустится через пару секунд"
        if action_id == "tasks":
            return True, f"перезапущено задач: {self._tasks.repair()}"
        if action_id == "reboot":
            r = await self._power.reboot()
            return r.ok, r.message
        r = await self._power.restart(action_id.removeprefix("svc:"))
        return r.ok, r.message

    def journal(self, user: int, action: str, ok: bool, detail: str, *, now: float | None = None) -> None:
        line = json.dumps({"ts": now or time.time(), "user": user, "action": action, "ok": ok, "detail": detail}, ensure_ascii=False)
        try:
            fd = os.open(self._log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            log.warning("Cannot write %s: %s", self._log, exc)
        log.warning("Repair: user=%s action=%s ok=%s %s", user, action, ok, detail)

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        try:
            lines = self._log.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
