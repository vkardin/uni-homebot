"""🌍 Каталог сообщества: шаблоны приложений для доски (название, значок, порт, как узнать контейнер).

Это только данные, никакого кода: шаблон помогает узнать запущенный контейнер и одним касанием добавить его
на доску приложений. Встроенная копия — `catalog/apps.json`; свежая берётся по `COMMUNITY_CATALOG_URL`
(раз в сутки, при ошибке — встроенная). Новые шаблоны присылают pull request'ом в этот файл.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
BUNDLED = Path(__file__).resolve().parents[2] / "catalog" / "apps.json"
TTL = 86400
MAX_SIZE = 256 * 1024
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
_cache: dict[str, Any] = {"ts": 0.0, "apps": None}


def parse(data: Any) -> list[dict[str, Any]]:
    """Проверенные шаблоны; кривые записи пропускаются."""
    if not isinstance(data, dict) or data.get("format") != "uni-catalog":
        return []
    out = []
    for a in data.get("apps") or []:
        if not isinstance(a, dict) or not _ID_RE.match(str(a.get("id", ""))):
            continue
        images = [str(i).lower() for i in a.get("images") or [] if isinstance(i, str) and i.strip()][:5]
        port = a.get("port")
        if not images or not isinstance(port, int) or not 0 < port < 65536:
            continue
        path = str(a.get("path", ""))
        out.append({
            "id": a["id"], "title": str(a.get("title", a["id"]))[:32], "icon": str(a.get("icon", "🧩"))[:4],
            "images": images, "port": port, "about": str(a.get("about", ""))[:80],
            "path": path if path.startswith("/") and len(path) < 64 else "",
        })  # fmt: skip
    return out


def bundled() -> list[dict[str, Any]]:
    try:
        return parse(json.loads(BUNDLED.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


async def load(http: Any, url: str, *, now: float | None = None) -> list[dict[str, Any]]:
    now = time.time() if now is None else now
    if _cache["apps"] is not None and now - _cache["ts"] < TTL:
        return _cache["apps"]
    apps: list[dict[str, Any]] = []
    if url and http is not None:
        import aiohttp

        try:
            async with http.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                raw = await resp.content.read(MAX_SIZE + 1)
                if len(raw) <= MAX_SIZE:
                    apps = parse(json.loads(raw.decode("utf-8")))
        except Exception as exc:  # сеть, формат — берём встроенный
            log.info("Community catalog unavailable: %s", exc)
    apps = apps or bundled()
    _cache.update(ts=now, apps=apps)
    return apps


def _port(ports: Any, wanted: int) -> int:
    public = None
    for p in ports if isinstance(ports, list) else []:
        if isinstance(p, dict) and p.get("PublicPort") and p.get("Type", "tcp") == "tcp":
            if p.get("PrivatePort") == wanted:
                return int(p["PublicPort"])
            public = public or int(p["PublicPort"])
    return public or wanted


def match(apps: list[dict[str, Any]], raw: Any) -> dict[str, str]:
    """{id шаблона: внутренний адрес} для запущенных контейнеров."""
    found: dict[str, str] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("State") != "running":
            continue
        image = str(item.get("Image", "")).lower()
        for a in apps:
            if a["id"] not in found and any(p in image for p in a["images"]):
                found[a["id"]] = f"http://127.0.0.1:{_port(item.get('Ports'), a['port'])}{a['path']}"
                break
    return found
