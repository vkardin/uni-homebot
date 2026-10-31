"""🛰 Другие машины: список агентов UNI, их метрики и копии бэкапов на них.

Список лежит в `STATE_DIR/agents.json` (права 600, там токены): имя, адрес агента, токен, принимает ли бэкапы.
Агент — `python -m homebot.agent` на другой машине (см. homebot/agent.py).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

FILE_NAME = "agents.json"
MAX_AGENTS = 16
_NAME_RE = re.compile(r"^[\w .-]{1,32}$")


@dataclass(frozen=True, slots=True)
class Agent:
    name: str
    url: str
    token: str
    backups: bool = False


def load(state_dir: Path) -> list[Agent]:
    try:
        data = json.loads((state_dir / FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for a in data if isinstance(data, list) else []:
        try:
            out.append(Agent(str(a["name"]), str(a["url"]), str(a["token"]), bool(a.get("backups"))))
        except (KeyError, TypeError):
            continue
    return out


def save(state_dir: Path, agents: list[Agent]) -> None:
    path = state_dir / FILE_NAME
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump([asdict(a) for a in agents], fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def make(name: str, url: str, token: str) -> Agent:
    name, url, token = name.strip(), url.strip().rstrip("/"), token.strip()
    if not _NAME_RE.match(name):
        raise ValueError("имя: буквы, цифры, пробел, точка, дефис; до 32 символов")
    if not re.match(r"^https?://[^\s/]+(:\d+)?$", url):
        raise ValueError("адрес вида http://имя-в-tailscale:8099")
    if len(token) < 16:
        raise ValueError("токен не короче 16 символов")
    return Agent(name, url, token)


def _headers(agent: Agent) -> dict[str, str]:
    return {"Authorization": f"Bearer {agent.token}"}


async def metrics(http: Any, agent: Agent) -> dict[str, Any]:
    import aiohttp

    async with http.get(f"{agent.url}/metrics", headers=_headers(agent), timeout=aiohttp.ClientTimeout(total=8)) as resp:
        if resp.status == 401:
            raise ValueError("неверный токен")
        resp.raise_for_status()
        return await resp.json()


async def push_backup(http: Any, agent: Agent, path: Path) -> None:
    import aiohttp

    with path.open("rb") as fh:
        async with http.put(f"{agent.url}/backups/{path.name}", data=fh, headers=_headers(agent),
                            timeout=aiohttp.ClientTimeout(total=600)) as resp:  # fmt: skip
            resp.raise_for_status()


def public(agent: Agent) -> dict[str, Any]:
    """Для панели — без токена."""
    return {"name": agent.name, "url": agent.url, "backups": agent.backups}
