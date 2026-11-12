"""10.1: сигнал «я жив» для облачного Сторожа (extras/watchdog)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("aiohttp.web")

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from homebot.jobs.heartbeat import HeartbeatJob, default_metrics
from homebot.services.envcheck import unknown_keys
from homebot.services.tasks import TaskManager
from tests.test_defaults51 import make

TOKEN = "s" * 24


def _server(status: int = 200) -> tuple[web.Application, list]:
    got: list = []

    async def beat(request: web.Request) -> web.Response:
        got.append((request.headers.get("Authorization"), await request.json()))
        return web.json_response({"ok": status == 200}, status=status)

    app = web.Application()
    app.router.add_post("/beat", beat)
    return app, got


def _job(http, url: str) -> HeartbeatJob:
    return HeartbeatJob(http=http, url=url, token=TOKEN, timezone="Europe/Kyiv", collect=lambda: {"cpu": 3.5, "uptime": 60})


def test_beat_sends_metrics_with_token():
    async def go():
        app, got = _server()
        async with TestServer(app) as server, ClientSession() as http:
            assert await _job(http, str(server.make_url("/")) + "/").tick()
        return got

    got = asyncio.run(go())
    assert got == [(f"Bearer {TOKEN}", {"cpu": 3.5, "uptime": 60, "tz": "Europe/Kyiv"})]


def test_beat_failures_do_not_raise():
    async def go():
        app, _ = _server(status=401)
        async with TestServer(app) as server, ClientSession() as http:
            rejected = await _job(http, str(server.make_url(""))).tick()
            url = str(server.make_url(""))
        async with ClientSession() as http:
            gone = await _job(http, url).tick()  # сервер уже остановлен
        return rejected, gone

    assert asyncio.run(go()) == (False, False)


def test_default_metrics_have_no_disk_list():
    m = default_metrics()
    assert "disks" not in m and {"cpu", "uptime", "version", "host"} <= set(m)


def test_settings_and_registration(tmp_path):
    from homebot.main import _register_watchdog

    s = make()
    assert s.watchdog_url == "" and s.watchdog_token is None and s.watchdog_interval_sec == 60
    with pytest.raises(ValueError):
        make(watchdog_url="uni-watchdog.example.workers.dev")
    store = SimpleNamespace(get=lambda k, d=None: d)

    tm = TaskManager(store)  # type: ignore[arg-type]
    assert not _register_watchdog(tm, s, http=None)
    assert not _register_watchdog(tm, make(watchdog_url="https://w.example.dev", watchdog_token="short"), http=None)
    assert tm.items() == []
    ok = make(watchdog_url="https://w.example.dev/", watchdog_token=TOKEN)
    assert ok.watchdog_url == "https://w.example.dev"
    assert _register_watchdog(tm, ok, http=None)
    assert [t.name for t in tm.items()] == ["watchdog"]

    env = tmp_path / ".env"
    env.write_text("WATCHDOG_URL=https://w\nWATCHDOG_TOKEN=x\nWATCHDOG_INTERVAL_SEC=60\n", encoding="utf-8")
    assert unknown_keys(env) == []
