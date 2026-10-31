"""🧹 Проверка .env: ключи, которых UNI не знает (опечатка или параметр, который больше не используется).

Только предупреждение в /doctor. Бот никогда не правит и не удаляет строки .env сам.
"""

from __future__ import annotations

import re
from pathlib import Path

from homebot.config import Settings

# Ключи, которые читает не сам бот: compose-шаблоны каталога, агент, системные.
_FOREIGN = ("CATALOG_", "UNI_AGENT_", "COMPOSE_", "DOCKER_HOST", "TZ", "PYTHON")
_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def unknown_keys(path: Path | str = ".env") -> list[str]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    known = {name.upper() for name in Settings.model_fields}
    found: list[str] = []
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        key = m.group(1).upper()
        if key in known or key.startswith(_FOREIGN) or key in found:
            continue
        found.append(key)
    return found
