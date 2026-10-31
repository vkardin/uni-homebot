"""🧱 Модули: любую подключённую плитку и любой встроенный раздел можно выключить, не забывая настроек.

Выключенное лежит в state.json (ключ `modules_off`): id плиток (кино, финансы…) и с 6.3 id разделов
(новости, напоминания, инструменты…). Id не пересекаются, поэтому старый список читается как есть.
Сервисы выключенных плиток в Apps обнуляются; у выключенных разделов прячутся кнопки меню, команды и вкладки
панели, а их фоновые задачи не запускаются (это — после перезапуска).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homebot.services.tiles import BY_ID, TILES

if TYPE_CHECKING:
    from homebot.services.apps import Apps
    from homebot.services.state import StateStore

KEY = "modules_off"


@dataclass(frozen=True, slots=True)
class Feature:
    """Встроенный раздел, который не требует подключения, но его можно выключить."""

    id: str
    title: str
    icon: str
    menu: str = ""  # имя MenuAction кнопки главного меню
    commands: tuple[str, ...] = ()
    jobs: tuple[str, ...] = ()  # фоновые задачи TaskManager
    panel: tuple[str, ...] = ()  # вкладки и разделы панели


FEATURES: tuple[Feature, ...] = (
    Feature("news", "Новости", "🗞", "NEWS", ("news",), ("news",), ("news",)),
    Feature("assistant", "Ассистент", "🤖", "OLLAMA", ("ollama", "why", "ask"), (), ("ai",)),
    Feature("reminders", "Напоминания", "⏰", "REMINDERS", ("remind", "reminders"), ("reminders",), ("reminders",)),
    Feature("notes", "Заметки", "📝", "NOTES", ("note", "notes"), (), ("notes",)),
    Feature("tools", "Инструменты", "🧰", "TOOLS", ("tools",), (), ("tools",)),
    Feature("weather", "Погодные предупреждения", "☔", jobs=("weather",)),
    Feature("games", "Бесплатные игры", "🎮", jobs=("games",)),
)
FEATURE_BY_ID: dict[str, Feature] = {f.id: f for f in FEATURES}
assert not set(FEATURE_BY_ID) & set(BY_ID), "id разделов и плиток не должны пересекаться"


def off(store: StateStore) -> set[str]:
    value = store.get(KEY, [])
    return {str(x) for x in value if str(x) in BY_ID or str(x) in FEATURE_BY_ID} if isinstance(value, list) else set()


def off_features(ids: set[str]) -> list[Feature]:
    return [f for f in FEATURES if f.id in ids]


def hidden_menu(ids: set[str]) -> set[str]:
    return {f.menu for f in off_features(ids) if f.menu}


def hidden_commands(ids: set[str]) -> set[str]:
    return {c for f in off_features(ids) for c in f.commands}


def off_jobs(ids: set[str]) -> set[str]:
    return {j for f in off_features(ids) for j in f.jobs}


def hidden_panel(ids: set[str]) -> list[str]:
    return sorted({p for f in off_features(ids) for p in f.panel})


async def set_off(store: StateStore, tile_id: str, disabled: bool) -> set[str]:
    ids = off(store)
    ids = ids | {tile_id} if disabled else ids - {tile_id}
    await store.set(KEY, sorted(ids))
    return ids


def off_services(store: StateStore) -> frozenset[str]:
    return frozenset(name for tile_id in off(store) if tile_id in BY_ID for name in BY_ID[tile_id].services)


def apply(apps: Apps, ids: set[str]) -> None:
    """Обнулить сервисы выключенных плиток."""
    for tile_id in ids:
        for name in BY_ID[tile_id].services if tile_id in BY_ID else ():
            setattr(apps, name, None)


def restore(apps: Apps, fresh: Apps, tile_id: str) -> None:
    """Вернуть сервисы плитки из заново собранного Apps (включение без перезапуска)."""
    for name in BY_ID[tile_id].services:
        setattr(apps, name, getattr(fresh, name))


def rows(apps: Apps, ids: set[str]) -> list[tuple[str, str]]:
    """(id, подпись) для списка: подключённые плитки и выключенные."""
    out = []
    for tile in TILES:
        if tile.id in ids:
            out.append((tile.id, f"⏸ {tile.icon} {tile.title} — выключен"))
        elif tile.connected(apps):
            out.append((tile.id, f"✅ {tile.icon} {tile.title}"))
    for f in FEATURES:
        out.append((f.id, f"⏸ {f.icon} {f.title} — выключен" if f.id in ids else f"✅ {f.icon} {f.title}"))
    return out


def title(module_id: str) -> str:
    item = BY_ID.get(module_id) or FEATURE_BY_ID.get(module_id)
    return f"{item.icon} {item.title}" if item is not None else module_id
