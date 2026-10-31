"""🔌 Подключение приложений из чата: поиск среди контейнеров, проверка связи и хранение настроек.

Настройки, введённые в чате, лежат в STATE_DIR/integrations.json (права 600: там ключи). `.env` всегда главнее:
параметр, заданный там явно, файл из чата не перекрывает.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from homebot.config import Settings
from homebot.services.apps import Apps
from homebot.services.tiles import CONNECTOR_BY_SERVICE, CONNECTORS, Connector

log = logging.getLogger(__name__)

FILE_NAME = "integrations.json"
Values = dict[str, str]


# ------------------------------------------------------------------ хранение
def load(state_dir: Path) -> dict[str, Values]:
    """{service: {поле Settings: значение}}; битый или чужой файл — пусто (и запись в журнал)."""
    path = state_dir / FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("Cannot read %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, Values] = {}
    for service, values in data.items():
        connector = CONNECTOR_BY_SERVICE.get(service)
        if connector is None or not isinstance(values, dict):
            continue
        allowed = {f.name for f in connector.fields} | ({connector.url_field} if connector.url_field else set())
        out[service] = {k: str(v) for k, v in values.items() if k in allowed}
    return out


def save(state_dir: Path, data: dict[str, Values]) -> None:
    """Атомарно и сразу с правами 600."""
    path = state_dir / FILE_NAME
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def explicit(settings: Settings, service: str) -> bool:
    """Приложение настроено в .env (тогда из чата его не меняем)."""
    connector = CONNECTOR_BY_SERVICE[service]
    names = [f.name for f in connector.fields if f.secret] or [f.name for f in connector.fields]
    if connector.enable_field:
        names = [connector.enable_field]
    return any(name in settings.model_fields_set for name in names)


def normalize_url(value: str) -> str | None:
    value = value.strip().rstrip("/")
    if not value.startswith(("http://", "https://")) or " " in value:
        return None
    return value


def apply(settings: Settings, data: dict[str, Values]) -> Settings:
    """Настройки с подставленными значениями из чата — только для того, что не задано в .env явно."""
    update: dict[str, Any] = {}
    for service, values in data.items():
        connector = CONNECTOR_BY_SERVICE.get(service)
        if connector is None or explicit(settings, service):
            continue
        secret = {f.name for f in connector.fields if f.secret}
        for name, value in values.items():
            if name in settings.model_fields_set:
                continue
            if name == connector.url_field:
                url = normalize_url(value)
                if url is None:
                    continue
                value = url
            update[name] = SecretStr(value) if name in secret else value
        if connector.enable_field:
            update[connector.enable_field] = True
    if not update:
        return settings
    merged = settings.model_copy()
    for name, value in update.items():  # object.__setattr__: значение из чата не считается заданным в .env
        object.__setattr__(merged, name, value)
    return merged


# ------------------------------------------------------------------ поиск
@dataclass(frozen=True, slots=True)
class Found:
    service: str
    container: str
    url: str


def match_containers(raw: Any) -> list[Found]:
    """Из ответа Docker /containers/json — какие известные приложения запущены и по какому адресу их искать."""
    found: list[Found] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("State") != "running":
            continue
        image = str(item.get("Image", "")).lower()
        names = [str(n).lstrip("/") for n in item.get("Names") or []]
        name = names[0] if names else image
        for connector in CONNECTORS:
            if connector.service in seen or not connector.images:
                continue
            if any(pattern in image for pattern in connector.images):
                found.append(Found(connector.service, name, guess_url(connector, item.get("Ports"))))
                seen.add(connector.service)
                break
    return found


def guess_url(connector: Connector, ports: Any) -> str:
    """Опубликованный порт приложения; без публикации (сеть хоста) — его обычный порт на 127.0.0.1."""
    public = None
    for port in ports if isinstance(ports, list) else []:
        if not isinstance(port, dict) or port.get("Type", "tcp") != "tcp" or not port.get("PublicPort"):
            continue
        if port.get("PrivatePort") == connector.port:
            public = port["PublicPort"]
            break
        public = public or port["PublicPort"]
    return f"http://127.0.0.1:{public or connector.port}"


# ------------------------------------------------------------------ проверка связи
async def _actual(apps: Apps) -> str:
    account, categories = await apps.actual.check()  # type: ignore[union-attr]
    return f"счёт «{account.name}», категорий {categories}"


async def _sites(apps: Apps) -> str:
    return f"слежек: {await apps.sites.check()}"  # type: ignore[union-attr]


async def _p2p(apps: Apps) -> str:
    quote = await apps.p2p.quote()  # type: ignore[union-attr]
    return f"{apps.p2p.asset}→{apps.p2p.fiat}: {quote.typical:.2f}"  # type: ignore[union-attr]


async def _call(coro: Awaitable[Any]) -> str:
    await coro
    return "на связи"


PROBES: dict[str, Callable[[Apps], Awaitable[str]]] = {
    "radarr": lambda a: _call(a.radarr.queue()),  # type: ignore[union-attr]
    "sonarr": lambda a: _call(a.sonarr.queue()),  # type: ignore[union-attr]
    "jellyfin": lambda a: _call(a.jellyfin.now_playing()),  # type: ignore[union-attr]
    "qbit": lambda a: _call(a.qbit.transfer()),  # type: ignore[union-attr]
    "adguard": lambda a: _call(a.adguard.stats()),  # type: ignore[union-attr]
    "speedtest": lambda a: _call(a.speedtest.latest()),  # type: ignore[union-attr]
    "sites": _sites,
    "hass": lambda a: a.hass.check(),  # type: ignore[union-attr]
    "actual": _actual,
    "p2p": _p2p,
}


async def probe(apps: Apps, service: str) -> str:
    """Проверка связи с подключённым сервисом; ошибка пробрасывается как есть."""
    if getattr(apps, service, None) is None:
        raise ValueError("не хватает параметров")
    return await PROBES[service](apps)


def forget(data: dict[str, Values], service: str) -> dict[str, Values]:
    out = dict(data)
    with suppress(KeyError):
        del out[service]
    return out
