"""9.1: Home Assistant."""

from __future__ import annotations

import asyncio

import pytest

from homebot.config import Settings
from homebot.services import connect, tiles
from homebot.services.apps import build_apps
from homebot.services.hass import HassService, as_json


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status = data, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def read(self):
        import json

        return json.dumps(self.data).encode()

    async def json(self, **kw):
        return self.data


class Session:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json")))
        if url.endswith("/api/states"):
            return Resp([
                {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Кухня"}},
                {"entity_id": "sensor.temp", "state": "21.5", "attributes": {"unit_of_measurement": "°C"}},
                {"entity_id": "automation.x", "state": "on", "attributes": {}},
                {"entity_id": "scene.movie", "state": "x", "attributes": {}},
            ])  # fmt: skip
        if "/api/states/" in url:
            return Resp({"state": "off"})
        return Resp({"location_name": "Дом"} if url.endswith("/api/config") else [])


def test_entities_and_actions():
    pytest.importorskip("aiohttp")
    s = Session()
    hass = HassService(s, "http://ha:8123/", "tok")  # type: ignore[arg-type]
    items = asyncio.run(hass.entities())
    assert [e.id for e in items] == ["light.kitchen", "scene.movie", "sensor.temp"]
    assert as_json(items[0])["can"] == "toggle" and as_json(items[1])["can"] == "run" and as_json(items[2])["can"] == ""
    asyncio.run(hass.act("light.kitchen"))
    asyncio.run(hass.act("scene.movie"))
    assert s.calls[-2][1].endswith("/api/services/light/toggle") and s.calls[-1][1].endswith("/api/services/scene/turn_on")
    with pytest.raises(Exception):  # noqa: B017 — датчик не переключается
        asyncio.run(hass.act("sensor.temp"))
    assert asyncio.run(hass.check()) == "Дом"


def test_tile_and_connector():
    s = Settings(bot_token="1:x", _env_file=None, hass_token="t")  # type: ignore[call-arg]
    apps = build_apps(None, s)  # type: ignore[arg-type]
    assert apps.hass is not None and "home" in tiles.connected_ids(apps) and "hass" in apps.available()
    assert "hass" in connect.PROBES and tiles.CONNECTOR_BY_SERVICE["hass"].port == 8123
    found = connect.match_containers(
        [{"State": "running", "Image": "ghcr.io/home-assistant/home-assistant:stable", "Names": ["/ha"], "Ports": []}]
    )
    assert found[0].service == "hass" and found[0].url == "http://127.0.0.1:8123"
