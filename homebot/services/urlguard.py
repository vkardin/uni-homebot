"""Проверка ссылок, которые вводят гости: сервер не должен ходить в домашнюю сеть или на себя по чужой просьбе."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

MAX_URL = 300


async def public_http_url(url: str) -> str:
    """Вернёт ссылку, если она ведёт на обычный сайт в интернете. Иначе ValueError с понятной причиной."""
    url = url.strip()
    if not url or len(url) > MAX_URL:
        raise ValueError("ссылка пустая или слишком длинная")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("нужна ссылка вида https://сайт/лента")
    if parts.username or parts.password:
        raise ValueError("ссылка с логином и паролем не подходит")
    host = parts.hostname.lower()
    if host == "localhost" or ("." not in host and ":" not in host):
        raise ValueError("адрес из домашней сети не подходит")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("сайт не найден") from exc
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            raise ValueError("не удалось проверить адрес") from None
        if not ip.is_global:
            raise ValueError("адрес из домашней сети не подходит")
    return url
