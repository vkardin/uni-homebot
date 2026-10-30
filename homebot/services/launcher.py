"""🚀 Доска приложений: веб-интерфейсы подключённых приложений и свои ссылки — одной сеткой на главной панели.

Адреса приложений в настройках обычно внутренние (http://127.0.0.1:порт) — с телефона они не откроются. Если панель
открыта по адресу Tailscale (WEB_URL), 127.0.0.1/localhost заменяется на имя сервера из WEB_URL: так приложение
открывается через Tailscale. Свои ссылки хранятся в state.json (ключ `launcher_links`).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from homebot.config import Settings
from homebot.services.tiles import CONNECTORS

KEY = "launcher_links"
MAX_LINKS = 24
ICONS = {
    "radarr": "🎬", "sonarr": "📺", "jellyfin": "🍿", "qbit": "🧲", "adguard": "🛡",
    "speedtest": "🚀", "sites": "🔎", "actual": "💰",
}  # fmt: skip
_LOCAL = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
_URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.IGNORECASE)


def reachable(url: str, web_url: str) -> str:
    """Внутренний адрес → адрес через имя сервера из WEB_URL (если WEB_URL задан)."""
    parts = urlsplit(url)
    host = urlsplit(web_url).hostname if web_url else None
    if not host or (parts.hostname or "") not in _LOCAL:
        return url
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def apps(settings: Settings, connected: set[str]) -> list[dict[str, Any]]:
    out = []
    for c in CONNECTORS:
        if c.service not in connected or not c.url_field:
            continue
        url = str(getattr(settings, c.url_field, "") or "")
        if url.startswith(("http://", "https://")):
            out.append(
                {"id": c.service, "title": c.title, "icon": ICONS.get(c.service, "🧩"), "url": reachable(url, settings.web_url)}
            )
    return out


def links(store: Any) -> list[dict[str, str]]:
    value = store.get(KEY, [])
    return [x for x in value if isinstance(x, dict) and _URL_RE.match(str(x.get("url", "")))] if isinstance(value, list) else []


def clean_link(title: str, url: str, icon: str = "") -> dict[str, str] | None:
    title, url, icon = title.strip()[:32], url.strip(), (icon.strip() or "🔗")[:4]
    if not title or len(url) > 500 or not _URL_RE.match(url):
        return None
    return {"title": title, "url": url, "icon": icon}
