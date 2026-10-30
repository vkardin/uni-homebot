"""Настройки в панели: новости хозяина (категории, источники) и личные настройки гостей (нужен aiohttp)."""

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("aiohttp.web")

from homebot.services.access import APPROVED, MAX_GUEST_FEEDS, AccessService
from homebot.services.prefs import Prefs
from homebot.services.presets import PRESETS
from homebot.services.state import StateStore
from homebot.web import settings_api
from homebot.web.api import ApiError

ADMIN = 1001
GOOD = "https://example.org/feed.xml"


class Req(dict):
    def __init__(self, ctx, body=None, guest=None):
        super().__init__()
        self.app = {"ctx": ctx}
        self._body = body
        if guest is not None:
            self["guest"] = guest

    async def json(self):
        return self._body


class FakeNews:
    def __init__(self):
        self.checked = []

    async def check_feed(self, url):
        self.checked.append(url)
        return ("example.org" in url or "ok." in url or url in {u for _, urls in PRESETS.values() for u in urls}), "Название"

    async def fetch_feed(self, url):
        from homebot.services.feeds import NewsItem

        return "Лента", [NewsItem(f"Новость {i} {url}", f"https://x.test/{i}", None, "текст") for i in range(12)]


def make(tmp):
    store = StateStore(Path(tmp) / "s.json")
    store.load()
    from homebot.config import Settings

    prefs = Prefs(store, Settings(_env_file=None, bot_token="1:x", admin_id=ADMIN, state_dir=Path(tmp)))
    access = AccessService(store, ADMIN)
    return SimpleNamespace(prefs=prefs, access=access, news=FakeNews(), store=store), prefs, access


def post(handler, ctx, body, guest=None):
    return asyncio.run(handler(Req(ctx, body, guest)))


def fails(handler, ctx, body, guest=None):
    try:
        post(handler, ctx, body, guest)
    except ApiError as exc:
        return exc
    raise AssertionError(f"{body} was accepted")


def test_admin_news_settings_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        ctx, prefs, _ = make(tmp)
        state = asyncio.run(settings_api.news_get(Req(ctx)))
        assert state["max"] == 30 and {p["key"] for p in state["presets"]} == set(PRESETS)
        out = post(
            settings_api.news_post, ctx, {"op": "set", "count": 7, "interval_min": 120, "quiet": False, "retell": False, "mode": "new"}
        )
        assert prefs.news().count == 7 and prefs.news().interval_min == 120 and not prefs.news().quiet and prefs.news().mode == "new"
        assert out["prefs"]["count"] == 7
        for bad in (
            {"op": "set", "count": 99},
            {"op": "set", "interval_min": 5},
            {"op": "set", "mode": "x"},
            {"op": "nope"},
            {"op": "preset", "key": "zzz"},
        ):
            assert fails(settings_api.news_post, ctx, bad)
        out = post(settings_api.news_post, ctx, {"op": "preset", "key": "tech", "on": True})
        assert next(p for p in out["presets"] if p["key"] == "tech")["active"] and len(prefs.news().feeds) == len(PRESETS["tech"][1])
        out = post(settings_api.news_post, ctx, {"op": "preset", "key": "tech", "on": False})
        assert prefs.news().feeds == () and not any(p["active"] for p in out["presets"])
        post(settings_api.news_post, ctx, {"op": "add", "url": GOOD})
        assert prefs.news().feeds == (GOOD,)
        assert "уже есть" in str(fails(settings_api.news_post, ctx, {"op": "add", "url": GOOD}))
        assert fails(settings_api.news_post, ctx, {"op": "add", "url": "ftp://x"})
        assert "не получилось" in str(fails(settings_api.news_post, ctx, {"op": "add", "url": "https://broken.test/rss"}))
        post(settings_api.news_post, ctx, {"op": "remove", "url": GOOD})
        assert prefs.news().feeds == ()


def test_guest_personal_settings_and_news():
    with tempfile.TemporaryDirectory() as tmp:
        ctx, _, access = make(tmp)
        asyncio.run(access.register(7, "Вася", None))
        guest = asyncio.run(access.set_status(7, APPROVED))
        state = asyncio.run(settings_api.my_get(Req(ctx, guest=guest)))
        assert state["news"] and state["nick"] == "" and state["count"] == 5
        guest = access.get(7)
        post(settings_api.my_post, ctx, {"op": "set", "nick": "  Василий  ", "count": 3}, guest)
        guest = access.get(7)
        assert guest.prefs == {"nick": "Василий", "count": 3}
        assert fails(settings_api.my_post, ctx, {"op": "set", "count": 4}, guest)
        post(settings_api.my_post, ctx, {"op": "preset", "key": "games", "on": True}, guest)
        guest = access.get(7)
        assert guest.prefs["presets"] == ["games"]
        urls = settings_api.personal_feed_urls(guest)
        assert urls == list(PRESETS["games"][1])
        items = asyncio.run(settings_api.personal_news(ctx, guest))
        assert items and len(items) <= 6 and all(i["feed"] == "Лента" and i["title"] for i in items)
        # личные ссылки: домашняя сеть и локальные адреса не принимаются, причина ошибки не раскрывается
        for bad in ("http://192.168.0.165:11434/", "http://localhost:8088/", "http://127.0.0.1/", "https://broken.test/rss"):
            err = fails(settings_api.my_post, ctx, {"op": "add", "url": bad}, guest)
            assert "HTTP" not in str(err) and err.status == 400
        post(settings_api.my_post, ctx, {"op": "preset", "key": "games", "on": False}, guest)
        assert access.get(7).prefs.get("presets") == []
        # свои источники ограничены
        guest = access.get(7)
        asyncio.run(access.set_prefs(7, feeds=[f"https://ok{i}.example.org/rss" for i in range(MAX_GUEST_FEEDS)]))
        assert "лимит" in str(fails(settings_api.my_post, ctx, {"op": "add", "url": "https://ok99.example.org/rss"}, access.get(7)))
        # без права на новости нельзя менять источники, но имя — можно
        asyncio.run(access.toggle(7, "news"))
        guest = access.get(7)
        assert fails(settings_api.my_post, ctx, {"op": "preset", "key": "ua", "on": True}, guest).status == 403
        post(settings_api.my_post, ctx, {"op": "set", "nick": "Вася"}, guest)
        assert not asyncio.run(settings_api.my_get(Req(ctx, guest=guest)))["news"]


def test_admin_has_no_personal_settings():
    with tempfile.TemporaryDirectory() as tmp:
        ctx, _, _ = make(tmp)
        assert asyncio.run(_raises(settings_api.my_get(Req(ctx)))).status == 404


async def _raises(coro):
    try:
        await coro
    except ApiError as exc:
        return exc
    raise AssertionError("no error")
