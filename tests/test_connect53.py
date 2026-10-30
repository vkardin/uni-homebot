"""5.3: подключение приложений из чата — хранение, .env главнее, поиск контейнеров, проверка связи."""

import asyncio
import os
import stat
import tempfile
from pathlib import Path

from homebot.config import Settings
from homebot.services import connect
from homebot.services.apps import Apps
from homebot.services.tiles import CONNECTOR_BY_SERVICE, CONNECTORS


def make(**overrides) -> Settings:
    return Settings(_env_file=None, **{"bot_token": "123:abc", "admin_id": 42, **overrides})  # type: ignore[call-arg]


def test_save_load_roundtrip_with_private_permissions_and_filtering():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert connect.load(root) == {}
        connect.save(root, {"radarr": {"radarr_url": "http://x:1", "radarr_api_key": "k", "junk": "1"}, "nope": {"a": "b"}})
        assert stat.S_IMODE(os.stat(root / connect.FILE_NAME).st_mode) == 0o600
        assert connect.load(root) == {"radarr": {"radarr_url": "http://x:1", "radarr_api_key": "k"}}
        (root / connect.FILE_NAME).write_text("{broken", encoding="utf-8")
        assert connect.load(root) == {}


def test_apply_fills_only_what_env_does_not_set():
    data = {"radarr": {"radarr_url": "http://10.0.0.2:7878/", "radarr_api_key": "chat"}, "p2p": {"p2p_fiat": "EUR"}}
    s = connect.apply(make(), data)
    assert s.radarr_url == "http://10.0.0.2:7878" and s.radarr_api_key.get_secret_value() == "chat"  # type: ignore[union-attr]
    assert s.p2p_enabled and s.p2p_fiat == "EUR"
    assert not connect.explicit(s, "radarr")  # значение из чата не считается заданным в .env
    env = connect.apply(make(radarr_api_key="env", p2p_enabled=False), data)
    assert env.radarr_api_key.get_secret_value() == "env" and env.radarr_url == "http://127.0.0.1:7878"  # type: ignore[union-attr]
    assert not env.p2p_enabled
    assert connect.apply(make(), {"radarr": {"radarr_url": "ftp://bad", "radarr_api_key": "k"}}).radarr_url == "http://127.0.0.1:7878"


def test_match_containers_and_url_guess():
    raw = [
        {
            "Image": "lscr.io/linuxserver/radarr:latest",
            "Names": ["/radarr"],
            "State": "running",
            "Ports": [{"PrivatePort": 7878, "PublicPort": 17878, "Type": "tcp"}],
        },
        {"Image": "lscr.io/linuxserver/sonarr", "Names": ["/sonarr"], "State": "exited", "Ports": []},
        {"Image": "jellyfin/jellyfin", "Names": ["/jf"], "State": "running", "Ports": []},
        {
            "Image": "adguard/adguardhome",
            "Names": ["/adguard"],
            "State": "running",
            "Ports": [{"PrivatePort": 53, "PublicPort": 53, "Type": "udp"}, {"PrivatePort": 3000, "PublicPort": 8880, "Type": "tcp"}],
        },
        {"Image": "postgres", "Names": ["/db"], "State": "running"},
        "garbage",
    ]
    found = {f.service: f for f in connect.match_containers(raw)}
    assert set(found) == {"radarr", "jellyfin", "adguard"}
    assert found["radarr"].url == "http://127.0.0.1:17878" and found["radarr"].container == "radarr"
    assert found["jellyfin"].url == "http://127.0.0.1:8096"  # сеть хоста: обычный порт
    assert found["adguard"].url == "http://127.0.0.1:8880"
    assert connect.match_containers({"message": "error"}) == []


def test_registry_consistency():
    apps_fields = set(Apps.__dataclass_fields__)
    settings_fields = set(Settings.model_fields)
    for c in CONNECTORS:
        assert c.service in apps_fields and c.service in connect.PROBES
        assert all(f.name in settings_fields for f in c.fields)
        assert not c.url_field or c.url_field in settings_fields
    assert CONNECTOR_BY_SERVICE["p2p"].enable_field == "p2p_enabled"


def test_probe_needs_service_and_reports_detail():
    class FakeRadarr:
        async def queue(self):
            return {}

    async def run():
        try:
            await connect.probe(Apps(), "radarr")
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError expected")
        return await connect.probe(Apps(radarr=FakeRadarr()), "radarr")  # type: ignore[arg-type]

    assert asyncio.run(run()) == "на связи"


def test_reader_endpoint_blocks_private_urls_and_is_open_to_guests_with_news():
    from homebot.web import api

    assert api._GUEST_PATHS["/api/read"] == "news"

    from types import SimpleNamespace

    try:
        asyncio.run(api.read(SimpleNamespace(query={"url": "http://127.0.0.1:7878/"})))  # type: ignore[arg-type]
    except api.ApiError:
        pass
    else:
        raise AssertionError("локальный адрес должен отклоняться")
