"""📦 Перенос настроек на другой сервер: один файл `uni-settings.json`.

В файл попадает то, что вы настраивали руками: настройки из панели (settings.json), новости и их источники,
ночной режим, P2P, доска приложений, выключенные модули. Токены, ключи приложений, люди и данные (заметки,
напоминания, история) не переносятся: для данных есть бэкап.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homebot import __version__
from homebot.services import overrides
from homebot.services.state import StateStore

FORMAT = "uni-settings"
FILE_NAME = "uni-settings.json"
MAX_SIZE = 256 * 1024
STATE_KEYS = (
    "prefs.news",
    "prefs.night",
    "prefs.p2p",
    "prefs.translate_model",
    "launcher_links",
    "modules_off",
    "setup_hidden",
)


class TransferError(ValueError):
    """Текст безопасно показывать пользователю."""


def is_settings_file(name: str | None) -> bool:
    name = (name or "").lower()
    return name.startswith("uni-settings") and name.endswith(".json")


def export(state_dir: Path, store: StateStore) -> bytes:
    state = {k: store.get(k) for k in STATE_KEYS if store.get(k) is not None}
    data = {"format": FORMAT, "version": __version__, "settings": overrides.load(state_dir), "state": state}
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def parse(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_SIZE:
        raise TransferError("файл слишком большой")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise TransferError("это не файл настроек UNI") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise TransferError("это не файл настроек UNI")
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    clean: dict[str, Any] = {}
    for name, value in settings.items():
        try:
            clean[name] = overrides.validate(name, value)
        except ValueError:
            continue  # чужое или ошибочное пропускаем
    return {
        "version": str(data.get("version", "?")),
        "settings": clean,
        "state": {k: v for k, v in state.items() if k in STATE_KEYS},
    }


async def apply(parsed: dict[str, Any], state_dir: Path, store: StateStore) -> list[str]:
    """Записывает настройки (поверх текущих). Возвращает, что изменилось."""
    done: list[str] = []
    if parsed["settings"]:
        overrides.save(state_dir, overrides.load(state_dir) | parsed["settings"])
        done.append(f"настройки панели: {len(parsed['settings'])}")
    for key, value in parsed["state"].items():
        await store.set(key, value)
    if parsed["state"]:
        done.append(f"разделы: {len(parsed['state'])}")
    return done
