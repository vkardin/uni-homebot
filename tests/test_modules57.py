"""5.7: модули — выключение плиток без потери настроек."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from homebot.services import modules, tiles
from homebot.services.state import StateStore


def _apps(**on):
    names = {name for t in tiles.TILES for name in t.services}
    return SimpleNamespace(**{n: on.get(n) for n in names})


def test_off_roundtrip(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.load()
    assert modules.off(store) == set()
    assert asyncio.run(modules.set_off(store, "movies", True)) == {"movies"}
    asyncio.run(store.set(modules.KEY, ["movies", "nope", 5]))
    assert modules.off(store) == {"movies"}  # неизвестное отбрасываем
    assert modules.off_services(store) == {"radarr", "sonarr"}
    assert asyncio.run(modules.set_off(store, "movies", False)) == set()


def test_apply_restore_rows():
    apps = _apps(radarr=object(), qbit=object())
    assert {tid for tid, _ in modules.rows(apps, set()) if tid in tiles.BY_ID} == {"movies", "qbit"}
    modules.apply(apps, {"movies"})
    assert apps.radarr is None and "movies" not in tiles.connected_ids(apps)  # type: ignore[arg-type]
    rows = dict(modules.rows(apps, {"movies"}))
    assert "выключен" in rows["movies"]
    fresh = _apps(radarr="R", sonarr="S")
    modules.restore(apps, fresh, "movies")
    assert (apps.radarr, apps.sonarr) == ("R", "S")


def test_panel_state(tmp_path):
    try:
        from homebot.web.api import _modules_state
    except ImportError:  # нет aiohttp
        return
    store = StateStore(tmp_path / "state.json")
    store.load()
    asyncio.run(modules.set_off(store, "qbit", True))
    ctx = SimpleNamespace(store=store, apps=_apps(radarr=object()), tasks=SimpleNamespace(broken=lambda: [("Кино", "boom")]))
    st = _modules_state(ctx)  # type: ignore[arg-type]
    assert st["items"][:2] == [
        {"id": "movies", "title": "🎬 Кино", "on": True, "kind": "app"},
        {"id": "qbit", "title": "🧲 qBittorrent", "on": False, "kind": "app"},
    ]
    assert st["items"][2]["kind"] == "section" and st["hidden"] == []
    assert st["broken"] == [{"title": "Кино", "error": "boom"}]


def test_sections_63(tmp_path):
    """6.3: встроенные разделы — тоже модули; старый список плиток читается как есть."""
    store = StateStore(tmp_path / "state.json")
    store.load()
    asyncio.run(store.set(modules.KEY, ["movies"]))  # как в 6.0
    assert modules.off(store) == {"movies"}
    asyncio.run(modules.set_off(store, "news", True))
    asyncio.run(modules.set_off(store, "tools", True))
    ids = modules.off(store)
    assert ids == {"movies", "news", "tools"}
    assert modules.off_services(store) == {"radarr", "sonarr"}
    assert modules.hidden_menu(ids) == {"NEWS", "TOOLS"}
    assert modules.hidden_commands(ids) == {"news", "tools"}
    assert modules.off_jobs(ids) == {"news"}
    assert modules.hidden_panel(ids) == ["news", "tools"]
    apps = _apps(radarr=object())
    modules.apply(apps, ids)  # разделы в Apps не трогаем
    rows = dict(modules.rows(apps, ids))
    assert "выключен" in rows["news"] and rows["notes"].startswith("✅")


def test_menu_hides_sections():
    from homebot.ui import keyboards as kb
    from homebot.ui.callbacks import MenuAction

    def actions():
        return {b.callback_data for row in kb.main_menu().inline_keyboard for b in row}

    kb.set_connected_tiles(set(tiles.BY_ID))
    before = actions()
    kb.set_sections_off({"NEWS", "BOGUS"})
    after = actions()
    assert len(after) == len(before) - 1
    kb.set_sections_off(set())
    assert actions() == before
    assert MenuAction.NEWS not in kb._HIDDEN


def test_tasks_unregister():
    from homebot.services.tasks import TaskManager

    tm = TaskManager(SimpleNamespace(get=lambda k, d=None: d))  # type: ignore[arg-type]

    async def job():
        return None

    tm.register("news", "Сводка", "", job, hidden=True)
    tm.unregister("news")
    tm.unregister("nope")
    tm.register("news", "Сводка", "", job, hidden=True)  # снова можно
