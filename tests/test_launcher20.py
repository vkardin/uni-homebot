"""2.0: 🚀 доска приложений."""

from __future__ import annotations

import asyncio

from homebot.config import Settings
from homebot.services import launcher
from homebot.services.state import StateStore


def test_reachable():
    web = "https://server.tail1234.ts.net"
    assert launcher.reachable("http://127.0.0.1:7878", web) == "http://server.tail1234.ts.net:7878"
    assert launcher.reachable("http://localhost:8096/web", web) == "http://server.tail1234.ts.net:8096/web"
    assert launcher.reachable("http://192.168.1.5:8080", web) == "http://192.168.1.5:8080"
    assert launcher.reachable("http://127.0.0.1:7878", "") == "http://127.0.0.1:7878"


def test_apps_only_connected():
    s = Settings(_env_file=None, bot_token="1:x", admin_id=1, radarr_url="http://127.0.0.1:7878", web_url="https://srv.ts.net")  # type: ignore[call-arg]
    items = launcher.apps(s, {"radarr"})
    assert items == [{"id": "radarr", "title": "Radarr", "icon": "🎬", "url": "http://srv.ts.net:7878"}]


def test_links(tmp_path):
    assert launcher.clean_link("", "https://x") is None
    assert launcher.clean_link("X", "javascript:alert(1)") is None
    assert launcher.clean_link(" Камеры ", "https://cam.local", "") == {"title": "Камеры", "url": "https://cam.local", "icon": "🔗"}
    store = StateStore(tmp_path / "s.json")
    store.load()
    asyncio.run(store.set(launcher.KEY, [{"title": "a", "url": "ftp://x"}, {"title": "b", "url": "https://b"}, 5]))
    assert launcher.links(store) == [{"title": "b", "url": "https://b"}]
