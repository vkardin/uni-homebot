from __future__ import annotations

import pytest

from homebot.services.launcher_tailscale import plan


def test_plan_stable_https_ports():
    items = plan(
        ["jellyfin", "radarr"],
        {"jellyfin": "http://localhost:8096/web", "radarr": "http://127.0.0.1:7878"},
        "home.tail123.ts.net.",
    )
    assert [(x.service, x.target, x.public_url) for x in items] == [
        ("jellyfin", "http://127.0.0.1:8096/web", "https://home.tail123.ts.net:18445"),
        ("radarr", "http://127.0.0.1:7878", "https://home.tail123.ts.net:18443"),
    ]


def test_plan_refuses_lan_target():
    with pytest.raises(ValueError, match="localhost"):
        plan(["radarr"], {"radarr": "http://192.168.1.10:7878"}, "home.ts.net")
