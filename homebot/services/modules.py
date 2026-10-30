"""🧱 Модули: любую подключённую плитку можно выключить, не забывая её настроек.

Выключенные плитки лежат в state.json (ключ `modules_off`). При запуске их сервисы в Apps обнуляются, поэтому
меню, команды, панель и фоновые задачи их не видят. Это зачаток модулей 6.0: плитка = модуль.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homebot.services.tiles import BY_ID, TILES

if TYPE_CHECKING:
    from homebot.services.apps import Apps
    from homebot.services.state import StateStore

KEY = "modules_off"


def off(store: StateStore) -> set[str]:
    value = store.get(KEY, [])
    return {str(x) for x in value if str(x) in BY_ID} if isinstance(value, list) else set()


async def set_off(store: StateStore, tile_id: str, disabled: bool) -> set[str]:
    ids = off(store)
    ids = ids | {tile_id} if disabled else ids - {tile_id}
    await store.set(KEY, sorted(ids))
    return ids


def off_services(store: StateStore) -> frozenset[str]:
    return frozenset(name for tile_id in off(store) for name in BY_ID[tile_id].services)


def apply(apps: Apps, ids: set[str]) -> None:
    """Обнулить сервисы выключенных плиток."""
    for tile_id in ids:
        for name in BY_ID[tile_id].services:
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
    return out
