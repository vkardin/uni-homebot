"""📦 Каталог приложений: бот только кладёт запрос, ставит контейнер root-служба (deploy/homebot_catalog.py).

Включается CATALOG_ENABLED=true и полной установкой (update.sh): без неё службы нет и запрос никто не возьмёт.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Item:
    name: str  # ключ белого списка в homebot_catalog.APPS
    service: str  # поле Apps / CONNECTORS
    title: str
    about: str
    after: str  # что сделать человеку после установки (если что-то нужно)


ITEMS: tuple[Item, ...] = (
    Item(
        "jellyfin",
        "jellyfin",
        "Jellyfin",
        "медиасервер: фильмы и сериалы на ТВ и телефоне",
        "Откройте Jellyfin (порт 8096), пройдите мастер и создайте API-ключ — потом подключите в 🧩 Приложениях.",
    ),
    Item("radarr", "radarr", "Radarr", "сам находит и скачивает фильмы", ""),
    Item("sonarr", "sonarr", "Sonarr", "то же для сериалов", ""),
    Item(
        "qbittorrent",
        "qbit",
        "qBittorrent",
        "торрент-клиент для Radarr и Sonarr",
        "Временный пароль веб-интерфейса — в журнале контейнера (🖥 Сервер → Docker → qbittorrent).",
    ),
    Item("changedetection", "sites", "changedetection.io", "слежка за ценами и страницами", ""),
)
BY_NAME: dict[str, Item] = {i.name: i for i in ITEMS}


def catalog_dir(state_dir: Path) -> Path:
    return state_dir / "catalog"


def available(state_dir: Path) -> bool:
    """Служба каталога установлена (update.sh создаёт каталог только при CATALOG_ENABLED=true)."""
    return catalog_dir(state_dir).is_dir()


def busy(state_dir: Path) -> bool:
    return (catalog_dir(state_dir) / "request.json").exists()


def request(state_dir: Path, name: str) -> None:
    if name not in BY_NAME:
        raise ValueError("такого приложения нет в каталоге")
    path = catalog_dir(state_dir)
    (path / "result.json").unlink(missing_ok=True)
    tmp = path / "request.tmp"
    tmp.write_text(json.dumps({"app": name}), encoding="utf-8")
    os.replace(tmp, path / "request.json")


def result(state_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((catalog_dir(state_dir) / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
