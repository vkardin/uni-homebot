"""7.3: каталог сообщества — шаблоны приложений (только данные)."""

from __future__ import annotations

import asyncio

from homebot.services import community


def test_bundled_is_valid():
    apps = community.bundled()
    assert len(apps) >= 10 and len({a["id"] for a in apps}) == len(apps)


def test_parse_rejects_junk():
    data = {"format": "uni-catalog", "apps": [
        {"id": "ok", "title": "Ok", "images": ["ok/img"], "port": 80, "path": "/admin"},
        {"id": "Bad ID", "images": ["x"], "port": 80},
        {"id": "noport", "images": ["x"]},
        {"id": "noimg", "port": 80},
        {"id": "evilpath", "images": ["x"], "port": 1, "path": "http://evil"},
        "junk",
    ]}  # fmt: skip
    apps = community.parse(data)
    assert [a["id"] for a in apps] == ["ok", "evilpath"] and apps[1]["path"] == ""
    assert community.parse({"format": "other"}) == [] and community.parse([]) == []


def test_match_ports():
    apps = community.parse({"format": "uni-catalog", "apps": [
        {"id": "ha", "images": ["home-assistant"], "port": 8123},
        {"id": "ph", "images": ["pihole/pihole"], "port": 80, "path": "/admin"},
    ]})  # fmt: skip
    raw = [
        {"State": "running", "Image": "ghcr.io/home-assistant/home-assistant:stable", "Ports": []},
        {"State": "running", "Image": "pihole/pihole:latest", "Ports": [{"PrivatePort": 80, "PublicPort": 8053, "Type": "tcp"}]},
        {"State": "exited", "Image": "other", "Ports": []},
    ]
    assert community.match(apps, raw) == {"ha": "http://127.0.0.1:8123", "ph": "http://127.0.0.1:8053/admin"}


def test_load_falls_back_to_bundled():
    community._cache.update(ts=0.0, apps=None)

    class Http:
        def get(self, *a, **k):
            raise OSError("offline")

    apps = asyncio.run(community.load(Http(), "https://x.test/apps.json", now=1000.0))
    assert apps == community.bundled()
    assert asyncio.run(community.load(None, "", now=1001.0)) is apps  # из кэша
    community._cache.update(ts=0.0, apps=None)
