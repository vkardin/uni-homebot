"""6.4: доска приложений — группы, порядок, статус «онлайн/офлайн»."""

from __future__ import annotations

import asyncio

from homebot.services import launcher


def _links():
    return [launcher.clean_link(t, f"https://{t}.test", "") for t in ("a", "b", "c")]


def test_clean_link_group():
    assert launcher.clean_link("NAS", "https://nas.test", "", " Дом ") == {
        "title": "NAS",
        "url": "https://nas.test",
        "icon": "🔗",
        "group": "Дом",
    }
    assert "group" not in launcher.clean_link("NAS", "https://nas.test")  # type: ignore[operator]


def test_move_and_regroup():
    items = _links()
    assert [x["title"] for x in launcher.move(items, "https://c.test", -1)] == ["a", "c", "b"]
    assert [x["title"] for x in launcher.move(items, "https://a.test", -1)] == ["a", "b", "c"]  # левее некуда
    assert [x["title"] for x in launcher.move(items, "https://a.test", 1)] == ["b", "a", "c"]
    assert launcher.move(items, "https://nope.test", 1) == items
    grouped = launcher.regroup(items, "https://b.test", "Дом")
    assert grouped[1]["group"] == "Дом" and "group" not in grouped[0]
    assert "group" not in launcher.regroup(grouped, "https://b.test", "")[1]


class _Resp:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Http:
    def __init__(self):
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        if "down" in url:
            raise OSError("refused")
        return _Resp(502 if "bad" in url else 401)


def test_status_cached():
    launcher._STATUS.clear()
    http = _Http()
    urls = ["http://up.test", "http://down.test", "http://bad.test"]
    assert asyncio.run(launcher.status(http, urls, now=100.0)) == {
        "http://up.test": True,
        "http://down.test": False,
        "http://bad.test": False,
    }
    asyncio.run(launcher.status(http, urls, now=130.0))
    assert len(http.calls) == 3  # из кэша
    asyncio.run(launcher.status(http, urls[:1], now=200.0))
    assert len(http.calls) == 4
