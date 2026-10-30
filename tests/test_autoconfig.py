"""deploy/autoconfig.py: ключи из конфигов контейнеров → integrations.json, без перезаписи заданного."""

import importlib.util
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location("autoconfig", Path(__file__).resolve().parents[1] / "deploy" / "autoconfig.py")
ac = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["autoconfig"] = ac  # dataclass ищет модуль по имени
_spec.loader.exec_module(ac)  # type: ignore[union-attr]


def _info(image, name, mount=None, ports=None, env=None, running=True):
    return {
        "Name": f"/{name}",
        "State": {"Running": running},
        "Config": {"Image": image, "Env": env or []},
        "Mounts": [{"Source": str(mount), "Destination": "/config"}] if mount else [],
        "NetworkSettings": {"Ports": ports or {}},
    }


def test_plan_extracts_keys_and_respects_env_and_stored():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "radarr").mkdir()
        (root / "radarr" / "config.xml").write_text("<Config><ApiKey>" + "a" * 32 + "</ApiKey></Config>")
        (root / "sonarr").mkdir()
        (root / "sonarr" / "config.xml").write_text("<Config><ApiKey>" + "b" * 32 + "</ApiKey></Config>")
        jf = root / "jf" / "data"
        jf.mkdir(parents=True)
        with sqlite3.connect(jf / "jellyfin.db") as db:
            db.execute("CREATE TABLE ApiKeys (AccessToken TEXT, DateCreated TEXT)")
            db.execute("INSERT INTO ApiKeys VALUES ('jfkey', '2024-01-01')")
        containers = [
            _info("lscr.io/linuxserver/radarr", "radarr", root / "radarr", {"7878/tcp": [{"HostIp": "0.0.0.0", "HostPort": "17878"}]}),
            _info("lscr.io/linuxserver/sonarr", "sonarr", root / "sonarr"),
            _info("jellyfin/jellyfin", "jellyfin", root / "jf"),
            _info("jhonderson/actual-http-api", "actual-api", env=["API_KEY=secret", "X=1"]),
            _info("lscr.io/linuxserver/radarr", "radarr2", root / "radarr"),  # второй такой же — пропуск
            _info("postgres", "db"),
            _info("qbittorrent", "qb", running=False),
        ]
        found = {app.service: values for app, _, values in ac.plan(containers, {"SONARR_API_KEY": "x"}, {"jellyfin": {}})}
        assert found["radarr"] == {"radarr_url": "http://127.0.0.1:17878", "radarr_api_key": "a" * 32}
        assert found["actual"] == {"actual_url": "http://127.0.0.1:5007", "actual_api_key": "secret"}
        assert "sonarr" not in found and "jellyfin" not in found  # .env главнее, сохранённое не трогаем
        assert ac.jellyfin_key(containers[2]) == {"jellyfin_api_key": "jfkey"}


def test_main_writes_private_file_and_env_parsing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        env = root / ".env"
        env.write_text("# comment\nRADARR_API_KEY=\nSONARR_API_KEY=abc\n")
        assert ac.env_values(env) == {"RADARR_API_KEY": "", "SONARR_API_KEY": "abc"}
        ac.save(root / ac.FILE_NAME, {"radarr": {"radarr_url": "http://127.0.0.1:7878"}})
        assert (root / ac.FILE_NAME).stat().st_mode & 0o777 == 0o600
        assert json.loads((root / ac.FILE_NAME).read_text())["radarr"]["radarr_url"] == "http://127.0.0.1:7878"


_cspec = importlib.util.spec_from_file_location(
    "homebot_catalog", Path(__file__).resolve().parents[1] / "deploy" / "homebot_catalog.py"
)
cat = importlib.util.module_from_spec(_cspec)  # type: ignore[arg-type]
_cspec.loader.exec_module(cat)  # type: ignore[union-attr]


def test_catalog_compose_is_fixed_whitelist():
    from homebot.services import catalog as bot_catalog
    from homebot.services.tiles import CONNECTOR_BY_SERVICE

    assert set(bot_catalog.BY_NAME) == set(cat.APPS)  # бот предлагает ровно то, что умеет root-служба
    assert all(i.service in CONNECTOR_BY_SERVICE for i in bot_catalog.ITEMS)
    data = json.loads(cat.compose_text("radarr", {"CATALOG_ROOT": "/data/uni", "TIMEZONE": "Europe/Kyiv"}))
    svc = data["services"]["radarr"]
    assert svc["image"].startswith("lscr.io/linuxserver/radarr") and svc["labels"] == {"uni.catalog": "1"}
    assert "/data/uni/radarr/config:/config" in svc["volumes"] and svc["environment"]["TZ"] == "Europe/Kyiv"
    assert "PUID" not in json.loads(cat.compose_text("changedetection", {}))["services"]["changedetection"]["environment"]


def test_bot_catalog_request_validates_name():
    from homebot.services import catalog as bot_catalog

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert not bot_catalog.available(root)
        (root / "catalog").mkdir()
        try:
            bot_catalog.request(root, "../../etc")
        except ValueError:
            pass
        else:
            raise AssertionError("ValueError expected")
        bot_catalog.request(root, "jellyfin")
        assert bot_catalog.busy(root) and json.loads((root / "catalog" / "request.json").read_text()) == {"app": "jellyfin"}
        (root / "catalog" / "result.json").write_text('{"app": "jellyfin", "ok": true}')
        assert bot_catalog.result(root) == {"app": "jellyfin", "ok": True}
