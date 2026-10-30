"""Доступные обновления apt и признак «нужна перезагрузка»."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from homebot.utils.proc import run_command

_UPGRADABLE_RE = re.compile(r"^(?P<name>[^/\s]+)/(?P<suites>\S+)\s+(?P<new>\S+)\s+\S+\s+\[upgradable from: (?P<old>[^\]]+)\]")


@dataclass(frozen=True, slots=True)
class UpdatesInfo:
    total: int = 0
    security: int = 0
    packages: tuple[str, ...] = ()
    reboot_required: bool = False
    reboot_packages: tuple[str, ...] = ()
    error: str | None = None

    @property
    def signature(self) -> tuple[int, int, bool]:
        """Для антиспама: уведомляем, только если картина изменилась."""
        return (self.total, self.security, self.reboot_required)


def parse_upgradable(output: str) -> tuple[list[str], int]:
    """Имена обновляемых пакетов и сколько из них — security."""
    names: list[str] = []
    security = 0
    for line in output.splitlines():
        match = _UPGRADABLE_RE.match(line)
        if not match:
            continue
        names.append(match["name"])
        if "-security" in match["suites"]:
            security += 1
    return names, security


class UpdatesService:
    def __init__(self, reboot_file: Path = Path("/var/run/reboot-required")) -> None:
        self._reboot_file = reboot_file

    def _reboot_state(self) -> tuple[bool, tuple[str, ...]]:
        if not self._reboot_file.exists():
            return False, ()
        try:
            text = Path(f"{self._reboot_file}.pkgs").read_text()
        except OSError:
            return True, ()
        return True, tuple(line.strip() for line in text.splitlines() if line.strip())

    async def check(self) -> UpdatesInfo:
        reboot, reboot_packages = await asyncio.to_thread(self._reboot_state)

        result = await run_command("apt", "list", "--upgradable", timeout=30)
        if not result.ok:
            return UpdatesInfo(reboot_required=reboot, reboot_packages=reboot_packages, error=result.problem)
        names, security = parse_upgradable(result.stdout)
        return UpdatesInfo(
            total=len(names),
            security=security,
            packages=tuple(names),
            reboot_required=reboot,
            reboot_packages=reboot_packages,
        )
