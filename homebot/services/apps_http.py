"""Общий HTTP-помощник для локальных веб-сервисов (Docker, Speedtest Tracker, AdGuard, Jellyfin, qBittorrent)."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

import aiohttp


class AppError(Exception):
    """Сервис недоступен или ответил ошибкой. Текст безопасно показывать пользователю."""


def basic_auth(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def describe_status(status: int) -> str:
    if status in (401, 403):
        return f"нет доступа (HTTP {status}) — проверьте логин/токен в .env"
    if status == 404:
        return "адрес не найден (HTTP 404) — проверьте URL в .env"
    return f"HTTP {status}"


def _error_detail(raw: bytes) -> str:
    """Текст ошибки из тела ответа (`{"error": "..."}` / `{"message": "..."}`), если сервис его прислал."""
    try:
        data = json.loads(raw[:4000])
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    message = data.get("error") or data.get("message")
    if not isinstance(message, str) or not message.strip():
        return ""
    return ": " + " ".join(message.split())[:160]


async def request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
    json_body: Any | None = None,
    data: Mapping[str, str] | None = None,
    timeout: float = 10,  # noqa: ASYNC109 - явный таймаут HTTP-запроса
    parse_json: bool = True,
) -> Any:
    """Один запрос с понятными ошибками. Возвращает JSON (или bytes, если parse_json=False)."""
    try:
        async with session.request(
            method,
            url,
            headers=dict(headers or {}),
            params=dict(params or {}),
            json=json_body,
            data=dict(data) if data is not None else None,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status >= 400:
                raise AppError(describe_status(resp.status) + _error_detail(await resp.read()))
            if not parse_json:
                return await resp.read()
            raw = await resp.read()
    except AppError:
        raise
    except TimeoutError as exc:
        raise AppError(f"нет ответа за {timeout:.0f} с") from exc
    except aiohttp.ClientConnectionError as exc:
        raise AppError("сервис недоступен (контейнер остановлен или неверный порт)") from exc
    except aiohttp.ClientError as exc:
        raise AppError(f"ошибка HTTP: {type(exc).__name__}") from exc
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise AppError("сервис вернул не JSON — проверьте URL в .env") from exc
