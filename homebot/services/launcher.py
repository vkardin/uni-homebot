"""🚀 Доска приложений: веб-интерфейсы подключённых приложений и свои ссылки — одной сеткой на главной панели.

С 6.4 у плиток есть точка «онлайн/офлайн» (проверка раз в минуту), свои ссылки раскладываются по группам
и переставляются (поле `group` и порядок в списке).

Адрес интеграции и адрес для браузера — разные вещи. Внутренний URL может быть loopback/Docker-именем, а клиентский
URL задаётся через LAUNCHER_URLS. Простая подмена localhost на имя сервера некорректна: на исходном порту может не
быть HTTPS-прокси. Свои ссылки хранятся в state.json (ключ `launcher_links`).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import urlsplit

from homebot.config import Settings
from homebot.services.tiles import CONNECTORS

KEY = "launcher_links"
MAX_LINKS = 24
ICONS = {
    "radarr": "🎬", "sonarr": "📺", "jellyfin": "🍿", "qbit": "🧲", "adguard": "🛡",
    "speedtest": "🚀", "sites": "🔎", "actual": "💰", "hass": "🏠",
}  # fmt: skip
_LOCAL = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
_URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.IGNORECASE)
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def public_urls(value: str) -> dict[str, str]:
    """Разобрать `service=https://…` из LAUNCHER_URLS; неверные элементы пропустить."""
    out: dict[str, str] = {}
    for item in re.split(r"[,\n]+", value):
        service, sep, url = item.strip().partition("=")
        url = url.strip().rstrip("/")
        if sep and _ID_RE.fullmatch(service) and _URL_RE.fullmatch(url):
            out[service] = url
    return out


def reachable(url: str, public_url: str = "") -> str:
    """Вернуть реально доступный браузеру URL; loopback без явного прокси не выдавать."""
    if public_url:
        return public_url
    parts = urlsplit(url)
    return "" if (parts.hostname or "").lower() in _LOCAL else url


def apps(settings: Settings, connected: set[str]) -> list[dict[str, Any]]:
    out = []
    exposed = public_urls(settings.launcher_urls)
    for c in CONNECTORS:
        if c.service not in connected or not c.url_field:
            continue
        url = str(getattr(settings, c.url_field, "") or "")
        if url.startswith(("http://", "https://")):
            out.append(
                {
                    "id": c.service,
                    "title": c.title,
                    "icon": ICONS.get(c.service, "🧩"),
                    "url": reachable(url, exposed.get(c.service, "")),
                    "probe": url,
                    "available": bool(reachable(url, exposed.get(c.service, ""))),
                }
            )
    return out


def links(store: Any) -> list[dict[str, str]]:
    value = store.get(KEY, [])
    return [x for x in value if isinstance(x, dict) and _URL_RE.match(str(x.get("url", "")))] if isinstance(value, list) else []


def clean_link(title: str, url: str, icon: str = "", group: str = "") -> dict[str, str] | None:
    title, url, icon, group = title.strip()[:32], url.strip(), (icon.strip() or "🔗")[:4], group.strip()[:24]
    if not title or len(url) > 500 or not _URL_RE.match(url):
        return None
    return {"title": title, "url": url, "icon": icon} | ({"group": group} if group else {})


def move(items: list[dict[str, str]], url: str, step: int) -> list[dict[str, str]]:
    """Сдвинуть ссылку на step позиций (в пределах списка)."""
    idx = next((i for i, x in enumerate(items) if x.get("url") == url), None)
    if idx is None:
        return items
    out = list(items)
    item = out.pop(idx)
    out.insert(max(0, min(len(out), idx + step)), item)
    return out


def regroup(items: list[dict[str, str]], url: str, group: str) -> list[dict[str, str]]:
    group = group.strip()[:24]
    out = []
    for x in items:
        if x.get("url") == url:
            x = {k: v for k, v in x.items() if k != "group"} | ({"group": group} if group else {})
        out.append(x)
    return out


_STATUS: dict[str, tuple[float, bool]] = {}
STATUS_TTL = 60.0


async def _probe(http: Any, url: str) -> bool:
    import aiohttp

    try:
        async with http.get(url, timeout=aiohttp.ClientTimeout(total=4), allow_redirects=False, ssl=False) as resp:
            return resp.status < 500
    except Exception:  # сеть, таймаут, неверный адрес — «офлайн»
        return False


async def status(http: Any, urls: list[str], *, now: float | None = None) -> dict[str, bool]:
    """{адрес: отвечает ли}; ответы кэшируются на минуту, чтобы панель не дёргала приложения при каждом открытии."""
    now = time.monotonic() if now is None else now
    todo = [u for u in dict.fromkeys(urls) if u not in _STATUS or now - _STATUS[u][0] > STATUS_TTL]
    for url, ok in zip(todo, await asyncio.gather(*(_probe(http, u) for u in todo)), strict=True):
        _STATUS[url] = (now, ok)
    return {u: _STATUS[u][1] for u in urls if u in _STATUS}
