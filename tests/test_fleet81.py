"""8.1–8.3: агент на других машинах, сводка и копии бэкапов."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("aiohttp.web")

from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from homebot import agent
from homebot.services import fleet

TOKEN = "t" * 24


def test_metrics_shape():
    m = agent.metrics()
    assert {"host", "cpu", "ram", "disk", "uptime", "version"} <= set(m)


def test_make_validates():
    assert fleet.make("NAS", "http://nas:8099/", TOKEN).url == "http://nas:8099"
    for args in [("", "http://x:1", TOKEN), ("NAS", "ftp://x", TOKEN), ("NAS", "http://x:1", "short"), ("a/b", "http://x", TOKEN)]:
        with pytest.raises(ValueError):
            fleet.make(*args)


def test_save_load(tmp_path):
    fleet.save(tmp_path, [fleet.Agent("NAS", "http://nas:8099", TOKEN, True)])
    assert fleet.load(tmp_path)[0].backups and (tmp_path / "agents.json").stat().st_mode & 0o077 == 0
    assert "token" not in fleet.public(fleet.load(tmp_path)[0])


def test_agent_roundtrip(tmp_path):
    async def go():
        server = TestServer(agent.build_app(TOKEN, tmp_path / "data", keep=2))
        await server.start_server()
        url = str(server.make_url("")).rstrip("/")
        a = fleet.Agent("me", url, TOKEN)
        async with ClientSession() as http:
            m = await fleet.metrics(http, a)
            assert m["host"]
            with pytest.raises(ValueError):
                await fleet.metrics(http, fleet.Agent("me", url, "x" * 20))
            for i in range(3):
                f = tmp_path / f"homebot-2026010{i}-000000.tar.gz"
                f.write_bytes(b"x" * 10)
                await fleet.push_backup(http, a, f)
            bad = tmp_path / "evil.tar.gz"
            bad.write_bytes(b"x")
            with pytest.raises(Exception):  # noqa: B017 — имя не по шаблону
                await fleet.push_backup(http, a, bad)
        await server.close()

    asyncio.run(go())
    kept = sorted(p.name for p in (tmp_path / "data").iterdir())
    assert kept == ["homebot-20260101-000000.tar.gz", "homebot-20260102-000000.tar.gz"]


def test_watch_alerts(tmp_path, monkeypatch):
    from homebot.jobs import fleet_watch

    sent: list[str] = []

    class Bot:
        async def send_message(self, chat, text):
            sent.append(text)

    class Store(dict):
        def get(self, k, d=None):
            return super().get(k, d)

        async def set(self, k, v):
            self[k] = v

    fleet.save(tmp_path, [fleet.Agent("NAS", "http://nas:1", TOKEN)])
    state = {"fail": True}

    async def fake_metrics(http, a):
        if state["fail"]:
            raise OSError("down")
        return {"disk": 95}

    monkeypatch.setattr(fleet, "metrics", fake_metrics)
    job = fleet_watch.FleetWatchJob(bot=Bot(), admin_id=1, store=Store(), http=None, state_dir=tmp_path, disk_limit=90)
    for _ in range(4):
        asyncio.run(job.tick())
    assert len(sent) == 1 and "не отвечает" in sent[0]
    state["fail"] = False
    asyncio.run(job.tick())
    asyncio.run(job.tick())
    assert "снова на связи" in sent[1] and "95%" in sent[2] and len(sent) == 3
