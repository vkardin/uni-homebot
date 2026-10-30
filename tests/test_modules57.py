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
    assert {tid for tid, _ in modules.rows(apps, set())} == {"movies", "qbit"}
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
    assert st["items"] == [{"id": "movies", "title": "🎬 Кино", "on": True}, {"id": "qbit", "title": "🧲 qBittorrent", "on": False}]
    assert st["broken"] == [{"title": "Кино", "error": "boom"}]
