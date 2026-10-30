"""Управление сервером: перезапуск разрешённых служб, перезагрузка, выключение.

Права выдаёт polkit-правило (его создаёт deploy/update.sh), а не sudo или root: у юнита включён
NoNewPrivileges. Список служб — строгий белый список из конфигурации.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from homebot.utils.proc import run_command

log = logging.getLogger(__name__)

_UNIT_RE = re.compile(r"[A-Za-z0-9:_.@\-]+")
CONFIRM_TTL_SEC = 60


@dataclass(frozen=True, slots=True)
class ActionResult:
    ok: bool
    message: str


def _hint(problem: str) -> str:
    lowered = problem.lower()
    if "authentication" in lowered or "access denied" in lowered or "not authorized" in lowered:
        return "нет прав (polkit-правило не установлено — запустите deploy/update.sh)"
    return problem


class PowerService:
    def __init__(self, restartable: Sequence[str], *, power_enabled: bool) -> None:
        self._restartable = tuple(u for u in restartable if _UNIT_RE.fullmatch(u) and not u.startswith("-"))
        self.power_enabled = power_enabled

    @property
    def restartable(self) -> tuple[str, ...]:
        return self._restartable

    async def restart(self, unit: str) -> ActionResult:
        if unit not in self._restartable:  # единственная проверка, на которую опирается безопасность
            return ActionResult(False, "служба не входит в список разрешённых")
        log.warning("Restarting service on user request: %s", unit)
        result = await run_command("systemctl", "--no-ask-password", "restart", "--", unit, timeout=60)
        if not result.ok:
            return ActionResult(False, _hint(result.problem))
        return ActionResult(True, "перезапущена")

    async def reboot(self) -> ActionResult:
        return await self._power("reboot")

    async def poweroff(self) -> ActionResult:
        return await self._power("poweroff")

    async def _power(self, verb: str) -> ActionResult:
        if not self.power_enabled:
            return ActionResult(False, "управление питанием отключено (POWER_ENABLED=false)")
        log.warning("Power action on user request: %s", verb)
        result = await run_command("systemctl", "--no-ask-password", verb, timeout=20)
        if not result.ok:
            return ActionResult(False, _hint(result.problem))
        return ActionResult(True, "команда отправлена")


def is_fresh(issued_at: int, now: float) -> bool:
    """Подтверждение действует `CONFIRM_TTL_SEC` секунд: старая кнопка «Да» не должна сработать завтра."""
    return 0 <= now - issued_at <= CONFIRM_TTL_SEC
