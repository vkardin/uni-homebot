"""6.1: «Начало работы» — шаги считаются по настоящему состоянию."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from homebot.config import Settings
from homebot.services import setup, tiles
from homebot.services.state import StateStore


def _apps(**on):
    names = {name for t in tiles.TILES for name in t.services}
    return SimpleNamespace(**{n: on.get(n) for n in names})


def _settings(**kw):
    return Settings(bot_token="1:x", admin_id=1, timezone="UTC", _env_file=None, **kw)  # type: ignore[call-arg]


def test_fresh_install_has_open_steps():
    items = setup.steps(_settings(), _apps())
    by = {s.id: s for s in items}
    assert by["owner"].done
    assert by["timezone"].done  # задан явно
    assert not by["weather"].done and not by["news"].done and not by["apps"].done and not by["panel"].done
    assert setup.progress(items) == (2, 6)
    text = setup.render(items)
    assert "готово 2 из 6" in text and "/connect" in text


def test_everything_done():
    s = _settings(weather_lat=50.0, weather_lon=30.0, web_url="https://x.ts.net")
    items = setup.steps(s, _apps(radarr=object()), ["https://feed"])
    assert setup.progress(items) == (6, 6)
    assert "всё готово" in setup.render(items)


def test_hidden_flag(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.load()
    assert not setup.hidden(store)
    asyncio.run(setup.set_hidden(store, True))
    data = setup.as_json(setup.steps(_settings(), _apps()), store)
    assert data["hidden"] and data["total"] == 6 and data["steps"][0]["id"] == "owner"
