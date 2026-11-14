"""10.2: управление сервером из панели — обновление, самопроверка, безопасность, сеть (нужен aiohttp)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("aiohttp.web")

from homebot.services import selfupdate, update_flow
from homebot.services.network import TailnetNode, TailnetStatus
from homebot.services.ssh_log import LoginRecord
from homebot.services.updates import UpdatesInfo
from homebot.web import api


class Req:
    def __init__(self, ctx, body=None, query=None, match=None):
        self.app = {"ctx": ctx}
        self._body = body
        self.query = query or {}
        self.match_info = match or {}

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def run(coro):
    return asyncio.run(coro)


def _ctx(tmp_path: Path, **kw):
    settings = SimpleNamespace(state_dir=tmp_path, homebot_in_container=False, update_repo="o/r", update_channel="stable",
                               update_require_signature=False)  # fmt: skip
    return SimpleNamespace(settings=settings, http=None, **kw)


def test_int_helper_gives_400_not_500():
    assert api._int("5", "x") == 5 and api._int(None, "x", 7) == 7 and api._int("", "x") == 0
    with pytest.raises(api.ApiError) as err:
        api._int("abc", "lines")
    assert err.value.status == 400


def test_update_post_needs_confirm_and_updater(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    with pytest.raises(api.ApiError, match="confirm"):
        run(api.control_update_post(Req(ctx, {"op": "install", "version": "1.0.0"})))
    monkeypatch.setattr(selfupdate, "updater_installed", lambda: False)
    with pytest.raises(api.ApiError, match="обновлятор"):
        run(api.control_update_post(Req(ctx, {"op": "install", "version": "1.0.0", "confirm": True})))
    monkeypatch.setattr(selfupdate, "updater_installed", lambda: True)
    with pytest.raises(api.ApiError, match="op"):
        run(api.control_update_post(Req(ctx, {"op": "rm -rf", "confirm": True})))


def test_rollback_writes_request(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(selfupdate, "updater_installed", lambda: True)
    monkeypatch.setattr(selfupdate, "previous_version", lambda: None)
    with pytest.raises(api.ApiError, match="прежней"):
        run(api.control_update_post(Req(ctx, {"op": "rollback", "confirm": True})))
    monkeypatch.setattr(selfupdate, "previous_version", lambda: "9.9.9")
    assert run(api.control_update_post(Req(ctx, {"op": "rollback", "confirm": True})))["ok"]
    assert selfupdate.request_pending(tmp_path)
    with pytest.raises(api.ApiError, match="уже"):
        run(api.control_update_post(Req(ctx, {"op": "rollback", "confirm": True})))


def test_update_status(tmp_path):
    data = run(api.control_update(Req(_ctx(tmp_path))))
    assert data["version"] and data["pending"] is False and "latest" not in data


def test_blocker(tmp_path):
    info = selfupdate.ArchiveInfo(version="1", sha256="ab" * 32, size=1, files=1, system_changed=("deploy/x",))
    assert "системные" in update_flow.blocker(info, tmp_path / "a.tar.gz", False)
    clean = selfupdate.ArchiveInfo(version="1", sha256="ab" * 32, size=1, files=1)
    assert update_flow.blocker(clean, tmp_path / "a.tar.gz", False) is None
    assert "подпись" in update_flow.blocker(clean, tmp_path / "a.tar.gz", True)


def test_security_and_network(tmp_path):
    class Ssh:
        async def recent_logins(self, limit):
            return [LoginRecord(None, "root", "1.2.3.4", "publickey")], None

        async def failed_count(self):
            return 3, None

    class Upd:
        async def check(self):
            return UpdatesInfo(total=2, security=1, packages=("a", "b"))

    class Net:
        async def tailscale(self):
            return TailnetStatus("Running", (TailnetNode("nas", "100.1.1.1", "linux", True),))

        async def external_ip(self):
            return "5.6.7.8"

        async def throughput(self):
            return 1.0, 2.0, 3, 4

    ctx = _ctx(tmp_path, ssh_log=Ssh(), updates=Upd(), smart=None, network=Net())
    sec = api.plain(run(api.control_security(Req(ctx))))
    assert sec["failed"] == 3 and sec["logins"][0]["user"] == "root" and sec["updates"]["total"] == 2
    net = api.plain(run(api.control_network(Req(ctx))))
    assert net["ip"] == "5.6.7.8" and net["tailnet"]["nodes"][0]["name"] == "nas"


def test_control_paths_are_admin_only():
    for path in ("/api/control/update", "/api/control/doctor", "/api/control/security", "/api/control/network"):
        assert api._guest_feature(path) is None
