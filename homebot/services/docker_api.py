"""Docker: список контейнеров и журналы через docker-socket-proxy (только чтение).

Перезапуск НЕ идёт через прокси: прокси умеет разрешить restart только вместе с POST=1, а это
открывает и создание контейнеров (фактически root). Вместо этого бот запускает root-юнит
`homebot-docker-restart@<имя>.service`, на что ему даёт право polkit-правило (см. deploy/update.sh).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import aiohttp

from homebot.services.apps_http import AppError, request
from homebot.utils.proc import run_command

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}")
_EXIT_RE = re.compile(r"Exited \((-?\d+)\)")
_LOG_TAIL_MAX = 3500


@dataclass(frozen=True, slots=True)
class Container:
    name: str
    image: str
    state: str  # running | exited | restarting | paused | created | dead
    status: str  # «Up 22 hours (healthy)», «Exited (137) 6 hours ago»

    @property
    def health(self) -> str | None:
        if "(unhealthy)" in self.status:
            return "unhealthy"
        if "(healthy)" in self.status:
            return "healthy"
        if "health: starting" in self.status:
            return "starting"
        return None

    @property
    def exit_code(self) -> int | None:
        match = _EXIT_RE.search(self.status)
        return int(match[1]) if match else None

    @property
    def problem(self) -> str | None:
        """Описание проблемы или None, если контейнер в порядке."""
        if self.state in ("exited", "dead"):
            code = self.exit_code
            if code == 137:
                return "остановлен (код 137 — убит, часто нехватка памяти)"
            return f"остановлен (код {code})" if code is not None else "остановлен"
        if self.state == "restarting":
            return "перезапускается по кругу"
        if self.health == "unhealthy":
            return "unhealthy — проверка здоровья не проходит"
        return None

    @property
    def emoji(self) -> str:
        if self.problem:
            return "🔴" if self.state != "running" else "🟠"
        if self.state == "paused":
            return "⏸"
        return "🟢"


def parse_containers(data: Any) -> list[Container]:
    if not isinstance(data, list):
        raise AppError("неожиданный ответ Docker API")
    result: list[Container] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        names = item.get("Names") or []
        name = str(names[0]).lstrip("/") if names else str(item.get("Id", "?"))[:12]
        result.append(
            Container(
                name=name,
                image=str(item.get("Image", "")),
                state=str(item.get("State", "")),
                status=str(item.get("Status", "")),
            )
        )
    # Проблемные сверху, затем по имени.
    result.sort(key=lambda c: (c.problem is None, c.name.lower()))
    return result


def demux_logs(raw: bytes) -> str:
    """Docker отдаёт журнал контейнера без TTY в мультиплексированном виде: 8-байтный заголовок на кадр."""
    if len(raw) >= 8 and raw[0] in (0, 1, 2) and raw[1:4] == b"\x00\x00\x00":
        parts: list[bytes] = []
        pos = 0
        while pos + 8 <= len(raw):
            size = int.from_bytes(raw[pos + 4 : pos + 8], "big")
            parts.append(raw[pos + 8 : pos + 8 + size])
            pos += 8 + size
        raw = b"".join(parts)
    return raw.decode(errors="replace")


class DockerService:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, *, restart_enabled: bool) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self.restart_enabled = restart_enabled

    async def containers(self) -> list[Container]:
        return parse_containers(await self.containers_raw())

    async def containers_raw(self) -> object:
        """Ответ Docker как есть (образы, порты) — для поиска приложений (services/connect.py)."""
        return await request(self._session, "GET", f"{self._base}/containers/json", params={"all": "true"})

    async def logs(self, name: str, tail: int = 60) -> str:
        if not NAME_RE.fullmatch(name):
            raise AppError("недопустимое имя контейнера")
        raw = await request(
            self._session,
            "GET",
            f"{self._base}/containers/{name}/logs",
            params={"stdout": "1", "stderr": "1", "tail": str(tail), "timestamps": "0"},
            parse_json=False,
            timeout=15,
        )
        text = demux_logs(raw).strip()
        return ("…" + text[-_LOG_TAIL_MAX:]) if len(text) > _LOG_TAIL_MAX else text

    async def restart(self, name: str) -> tuple[bool, str]:
        if not self.restart_enabled:
            return False, "перезапуск контейнеров отключён (DOCKER_RESTART_ENABLED=false)"
        if not NAME_RE.fullmatch(name):
            return False, "недопустимое имя контейнера"
        result = await run_command("systemctl", "--no-ask-password", "start", f"homebot-docker-restart@{name}.service", timeout=120)
        if result.ok:
            return True, "перезапущен"
        problem = result.problem
        if "authentication" in problem.lower() or "access denied" in problem.lower():
            problem = "нет прав (запустите deploy/update.sh — он создаст polkit-правило)"
        return False, problem
