"""👑 Владелец бота без ADMIN_ID: захват по одноразовому коду из журнала сервера.

Если ADMIN_ID в .env не задан, бот при первом запуске придумывает 6-значный код, пишет его в журнал
(journalctl -u homebot) и в STATE_DIR/owner-code.txt (права 600) и ждёт `/start <код>`. Кто первым прислал
верный код — тот владелец; его ID сохраняется в STATE_DIR/owner.json, и дальше бот работает как с ADMIN_ID.
ADMIN_ID из .env всегда главнее файла.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

FILE_NAME = "owner.json"
CODE_FILE = "owner-code.txt"
MAX_TRIES_PER_USER = 5
MAX_TRIES_TOTAL = 50  # после стольких неверных попыток код меняется


def load(state_dir: Path) -> int | None:
    try:
        data = json.loads((state_dir / FILE_NAME).read_text(encoding="utf-8"))
        owner = int(data["owner_id"])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError) as exc:
        log.warning("Cannot read %s: %s", state_dir / FILE_NAME, exc)
        return None
    return owner if owner > 0 else None


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def save(state_dir: Path, owner_id: int) -> None:
    _write_private(state_dir / FILE_NAME, json.dumps({"owner_id": owner_id}))
    (state_dir / CODE_FILE).unlink(missing_ok=True)


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


@dataclass
class Claim:
    """Проверка кода: попытки считаются по пользователю и всего; при переборе код меняется."""

    state_dir: Path
    code: str = field(default_factory=new_code)
    tries: dict[int, int] = field(default_factory=dict)
    total: int = 0

    def publish(self) -> None:
        _write_private(self.state_dir / CODE_FILE, self.code + "\n")
        log.warning("Владелец не назначен. Отправьте боту: /start %s  (код также в %s)", self.code, self.state_dir / CODE_FILE)

    def check(self, user_id: int, text: str) -> str:
        """'ok' — владелец найден; 'no_code' — /start без кода; 'wrong'; 'blocked' — попытки кончились."""
        parts = text.strip().split()
        if not parts or parts[0].split("@", 1)[0] != "/start":
            return "no_code"
        if self.tries.get(user_id, 0) >= MAX_TRIES_PER_USER:
            return "blocked"
        if len(parts) < 2:
            return "no_code"
        if secrets.compare_digest(parts[1], self.code):
            return "ok"
        self.tries[user_id] = self.tries.get(user_id, 0) + 1
        self.total += 1
        if self.total >= MAX_TRIES_TOTAL:
            self.code, self.total = new_code(), 0
            self.publish()
        return "wrong"
