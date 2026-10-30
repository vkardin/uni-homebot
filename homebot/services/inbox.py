"""🔔 Центр уведомлений: последние сообщения фоновых задач — чтобы пролистать их в панели.

Пишет middleware Telegram-сессии (utils/inbox_middleware.py): сохраняется всё, что фоновые задачи отправили
администратору (важность NORMAL и CRITICAL). Ответы на ваши действия сюда не попадают. Хранится в памяти и в
STATE_DIR/inbox.json (последние LIMIT записей), HTML-разметка снимается.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FILE_NAME = "inbox.json"
LIMIT = 100
TEXT_MAX = 600
_TAG_RE = re.compile(r"<[^>]+>")


def plain(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).strip()[:TEXT_MAX]


class Inbox:
    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / FILE_NAME
        self._items: list[dict[str, Any]] = []
        self._seen = 0.0
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._items = [i for i in data.get("items", []) if isinstance(i, dict)][-LIMIT:]
            self._seen = float(data.get("seen", 0))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            log.warning("Cannot read %s: %s", self._path, exc)

    def add(self, text: str, *, critical: bool = False, now: float | None = None) -> None:
        body = plain(text)
        if not body:
            return
        self._items.append({"ts": now if now is not None else time.time(), "text": body, "critical": critical})
        del self._items[:-LIMIT]
        self._save()

    def items(self) -> list[dict[str, Any]]:
        """Новые сверху; unread — пришло после последнего просмотра."""
        return [{**i, "unread": i["ts"] > self._seen} for i in reversed(self._items)]

    def unread(self) -> int:
        return sum(1 for i in self._items if i["ts"] > self._seen)

    def mark_read(self, now: float | None = None) -> None:
        self._seen = now if now is not None else time.time()
        self._save()

    def clear(self) -> None:
        self._items.clear()
        self._save()

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"seen": self._seen, "items": self._items}, fh, ensure_ascii=False)
            os.replace(tmp, self._path)
        except OSError as exc:  # центр уведомлений — удобство, бот из-за него не падает
            log.warning("Cannot write %s: %s", self._path, exc)
