"""Поддельные HTTP-сессия и ответы для тестов сервисов (без сети)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


class FakeContent:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def iter_chunked(self, size: int):
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]

    def __aiter__(self):
        """Построчная итерация, как у aiohttp.StreamReader (для потоковых ответов)."""

        async def lines():
            for line in self._body.splitlines(keepends=True):
                yield line

        return lines()


class FakeResponse:
    def __init__(self, status: int = 200, body: Any = None, raw: bytes | None = None, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}
        self.charset = "utf-8"
        self.cookies: dict[str, Any] = {}
        if raw is not None:
            self._raw = raw
        else:
            self._raw = json.dumps(body if body is not None else {}).encode()
        self.content = FakeContent(self._raw)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def read(self) -> bytes:
        return self._raw

    async def text(self) -> str:
        return self._raw.decode(errors="replace")

    async def json(self, content_type: str | None = None) -> Any:
        return json.loads(self._raw)

    def raise_for_status(self) -> None:
        if self.status >= 400:
            import aiohttp

            raise aiohttp.ClientResponseError(type("RI", (), {"real_url": "http://fake/"})(), (), status=self.status)  # type: ignore[arg-type]


class FakeSession:
    """`handler(method, url, kwargs)` возвращает FakeResponse или бросает исключение."""

    def __init__(self, handler: Callable[[str, str, dict[str, Any]], FakeResponse]) -> None:
        self._handler = handler
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return self._handler(method, url, kwargs)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("POST", url, **kwargs)
