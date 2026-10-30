"""Последние строки журнала службы и промпт для разбора журнала моделью."""

from __future__ import annotations

import re
from collections.abc import Sequence

from homebot.utils.proc import run_command

_UNIT_RE = re.compile(r"[A-Za-z0-9:_.@\-]+")
_MAX_CHARS = 3500

EXPLAIN_PROMPT = (
    "Ты — опытный Linux-администратор. Тебе дают последние строки журнала systemd одной службы. "
    "Кратко, по-русски: 1) что происходит, 2) есть ли ошибки и в чём их вероятная причина, "
    "3) что проверить или сделать (конкретные команды). Если проблем нет — так и скажи. "
    "Не выдумывай того, чего нет в журнале."
)


class LogsService:
    def __init__(self, units: Sequence[str]) -> None:
        seen: list[str] = []
        for unit in units:
            if _UNIT_RE.fullmatch(unit) and not unit.startswith("-") and unit not in seen:
                seen.append(unit)
        self._units = tuple(seen)

    @property
    def units(self) -> tuple[str, ...]:
        return self._units

    async def tail(self, unit: str, lines: int = 40) -> tuple[str, str | None]:
        """(текст журнала, ошибка). Показываем только юниты из списка — произвольные имена не принимаем."""
        if unit not in self._units:
            return "", "служба не входит в список"
        result = await run_command(
            "journalctl", "--no-pager", "-u", unit, "-n", str(lines), "-o", "short-iso", "--no-hostname", timeout=15
        )
        if not result.ok:
            return "", result.problem
        if "insufficient permissions" in result.stderr.lower():
            return "", "нет доступа к журналу (нужна группа systemd-journal)"
        text = result.stdout.strip()
        if len(text) > _MAX_CHARS:
            text = "…" + text[-_MAX_CHARS:]  # хвост важнее начала
        return text, None
