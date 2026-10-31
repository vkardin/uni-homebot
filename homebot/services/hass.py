"""🏠 Home Assistant: состояния устройств и переключение света, розеток и сценариев через REST API.

Нужен долгоживущий токен: профиль Home Assistant → Безопасность → Долгосрочные токены доступа.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp

from homebot.services.apps_http import AppError, request

DOMAINS = ("light", "switch", "fan", "cover", "climate", "scene", "script", "sensor", "binary_sensor", "lock")
TOGGLE = {"light", "switch", "fan", "input_boolean"}
RUN = {"scene", "script"}
MAX_ENTITIES = 120


@dataclass(frozen=True, slots=True)
class Entity:
    id: str
    name: str
    domain: str
    state: str
    unit: str = ""

    @property
    def on(self) -> bool:
        return self.state in {"on", "open", "unlocked", "playing", "heat", "cool"}


class HassService:
    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session, self._url, self._headers = session, url.rstrip("/"), {"Authorization": f"Bearer {token}"}

    async def check(self) -> str:
        data = await request(self._session, "GET", f"{self._url}/api/config", headers=self._headers)
        return str(data.get("location_name") or "Home Assistant") if isinstance(data, dict) else "Home Assistant"

    async def entities(self) -> list[Entity]:
        data = await request(self._session, "GET", f"{self._url}/api/states", headers=self._headers, timeout=15)
        out = []
        for item in data if isinstance(data, list) else []:
            eid = str(item.get("entity_id", "")) if isinstance(item, dict) else ""
            domain = eid.split(".", 1)[0]
            if domain not in DOMAINS and domain != "input_boolean":
                continue
            attrs = item.get("attributes") or {}
            out.append(Entity(eid, str(attrs.get("friendly_name") or eid), domain, str(item.get("state", "")),
                              str(attrs.get("unit_of_measurement") or "")))  # fmt: skip
        return sorted(out, key=lambda e: (DOMAINS.index(e.domain) if e.domain in DOMAINS else 0, e.name))[:MAX_ENTITIES]

    async def act(self, entity_id: str) -> None:
        """Переключить (свет, розетка) или запустить (сцена, скрипт)."""
        domain = entity_id.split(".", 1)[0]
        if domain in TOGGLE:
            service = f"{domain}/toggle"
        elif domain in RUN:
            service = f"{domain}/turn_on"
        else:
            raise AppError("это устройство только показывает состояние")
        await request(self._session, "POST", f"{self._url}/api/services/{service}", headers=self._headers,
                      json_body={"entity_id": entity_id})  # fmt: skip

    async def state(self, entity_id: str) -> str:
        data = await request(self._session, "GET", f"{self._url}/api/states/{entity_id}", headers=self._headers)
        return str(data.get("state", "")) if isinstance(data, dict) else ""


def as_json(e: Entity) -> dict[str, Any]:
    return {"id": e.id, "name": e.name, "domain": e.domain, "state": e.state, "unit": e.unit, "on": e.on,
            "can": "toggle" if e.domain in TOGGLE else "run" if e.domain in RUN else ""}  # fmt: skip
