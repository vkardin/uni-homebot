"""Распознавание «кинутого и забытого» в сообщении: magnet-ссылки и ссылки на страницы. Без aiogram."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

MAGNET_RE = re.compile(r"magnet:\?xt=urn:[a-z0-9]+:[^\s]+", re.IGNORECASE)
ONLY_URLS_RE = re.compile(r"^(?:\s*https?://\S+\s*)+$", re.IGNORECASE)


def magnet_name(magnet: str) -> str:
    name = parse_qs(urlparse(magnet).query).get("dn", [""])[0]
    return unquote(name)[:80] or "без названия"
