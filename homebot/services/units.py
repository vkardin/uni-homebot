"""Статус systemd-юнитов через `systemctl show` (без прав root)."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Имя юнита уходит в аргументы процесса, поэтому валидируем его строго.
_UNIT_RE = re.compile(r"[A-Za-z0-9:_.@\-]+")


# Частые ответы systemctl — коротко и по-русски, вместо обрезанной английской строки
_KNOWN_ERRORS = (
    ("not been booted with systemd", "systemd недоступен (контейнер?)"),
    ("failed to connect to bus", "нет связи с systemd"),
    ("could not be found", "служба не найдена"),
    ("access denied", "нет прав"),
)


def friendly_error(stderr: str, returncode: int | None = None) -> str:
    text = " ".join(stderr.split())
    low = text.lower()
    for needle, label in _KNOWN_ERRORS:
        if needle in low:
            return label
    if not text:
        return f"код {returncode}"
    return text if len(text) <= 80 else text[:79].rsplit(" ", 1)[0] + "…"


@dataclass(frozen=True, slots=True)
class UnitStatus:
    name: str
    load_state: str = "unknown"
    active_state: str = "unknown"
    sub_state: str = ""
    error: str | None = None

    @property
    def is_active(self) -> bool:
        return self.active_state == "active"

    @property
    def is_failed(self) -> bool:
        return self.active_state == "failed"

    @property
    def emoji(self) -> str:
        if self.error:
            return "❔"
        if self.load_state == "not-found":
            return "❔"
        return {"active": "🟢", "activating": "🟡", "reloading": "🟡", "failed": "🔴"}.get(self.active_state, "⚪")

    @property
    def label(self) -> str:
        if self.error:
            return f"ошибка: {self.error}"
        if self.load_state == "not-found":
            return "юнит не найден"
        return f"{self.active_state} ({self.sub_state})" if self.sub_state else self.active_state


class UnitsService:
    def __init__(self, units: Sequence[str], *, timeout: float = 5.0) -> None:
        valid: list[str] = []
        for unit in units:
            if _UNIT_RE.fullmatch(unit) and not unit.startswith("-"):
                valid.append(unit)
            else:
                log.warning("Ignoring invalid unit name: %r", unit)
        self._units = tuple(valid)
        self._timeout = timeout

    @property
    def units(self) -> tuple[str, ...]:
        return self._units

    async def status_all(self) -> list[UnitStatus]:
        if not self._units:
            return []
        return list(await asyncio.gather(*(self.status(unit) for unit in self._units)))

    async def status(self, unit: str) -> UnitStatus:
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl",
                "show",
                "--no-pager",
                "--property=LoadState,ActiveState,SubState",
                "--",
                unit,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return UnitStatus(unit, error="systemctl не найден")
        except OSError as exc:
            return UnitStatus(unit, error=str(exc)[:80])

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), self._timeout)
        except TimeoutError:
            with suppress(ProcessLookupError):  # процесс мог завершиться между таймаутом и kill
                proc.kill()
            await proc.wait()
            return UnitStatus(unit, error="таймаут systemctl")

        if proc.returncode != 0:
            return UnitStatus(unit, error=friendly_error(stderr.decode(errors="replace"), proc.returncode))

        props: dict[str, str] = {}
        for line in stdout.decode(errors="replace").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                props[key] = value
        return UnitStatus(
            unit,
            load_state=props.get("LoadState", "unknown"),
            active_state=props.get("ActiveState", "unknown"),
            sub_state=props.get("SubState", ""),
        )
