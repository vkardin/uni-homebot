"""Крошечное JSON-хранилище настроек, переживающих перезапуск (какие задачи включены, модель Ollama)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class StateStore:
    """Ключ -> JSON-значение. Запись атомарная (tmp-файл + `os.replace`).

    Ошибки записи логируются, но не роняют бота: состояние — удобство, а не критичные данные.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    def load(self) -> None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            log.warning("Cannot read state file %s: %s", self._path, exc)
            return

        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("root is not an object")
        except ValueError as exc:
            backup = self._path.with_suffix(".corrupt")
            log.warning("State file %s is corrupt (%s); moving it to %s", self._path, exc, backup)
            with suppress(OSError):
                self._path.replace(backup)
            return

        self._data = data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            if key in self._data and self._data[key] == value:
                return
            self._data[key] = value
            snapshot = dict(self._data)
            try:
                await asyncio.to_thread(self._write, snapshot)
            except OSError as exc:
                log.error("Cannot persist state to %s: %s", self._path, exc)

    async def replace_all(self, data: dict[str, Any]) -> None:
        """Подменяет всё состояние целиком (восстановление из бэкапа)."""
        async with self._lock:
            self._data = dict(data)
            snapshot = dict(self._data)
            await asyncio.to_thread(self._write, snapshot)

    def snapshot(self) -> dict[str, Any]:
        return dict(self._data)

    def _write(self, snapshot: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
