"""Настройки в панели: новости (категории и источники) для администратора и личные настройки для каждого человека."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp
from aiohttp import web

from homebot.services.access import GUEST_COUNTS, MAX_GUEST_FEEDS, Person
from homebot.services.feeds import FeedParseError
from homebot.services.news import feed_name
from homebot.services.prefs import COUNT_CHOICES, INTERVAL_CHOICES, MODE_DAY, MODE_NEW
from homebot.services.presets import PRESET_KEYS, PRESETS
from homebot.services.urlguard import public_http_url
from homebot.web.api import ApiError, _ctx, _json, _need

MAX_FEEDS = 30  # как в боте
routes = web.RouteTableDef()

_CACHE: dict[
    str, tuple[float, str, list[Any]]
] = {}  # лента → (когда, название, записи): гости не должны дёргать сайты чаще раза в 10 минут
_CACHE_TTL = 600
_CACHE_MAX = 200


# ----------------------------------------------------------------------- общее
def _preset_json(key: str, feeds: set[str]) -> dict[str, Any]:
    label, urls = PRESETS[key]
    return {"key": key, "label": label, "total": len(urls), "active": all(u in feeds for u in urls)}


def _feed_json(url: str) -> dict[str, str]:
    return {"url": url, "name": feed_name(url)}


async def _cached_feed(c: Any, url: str) -> tuple[str, list[Any]] | None:
    now = time.monotonic()
    hit = _CACHE.get(url)
    if hit is not None and now - hit[0] < _CACHE_TTL:
        return hit[1], hit[2]
    try:
        title, items = await c.news.fetch_feed(url)
    except (aiohttp.ClientError, TimeoutError, FeedParseError, ValueError):
        return (hit[1], hit[2]) if hit is not None else None
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[url] = (now, title, items)
    return title, items


def personal_feed_urls(person: Person) -> list[str]:
    urls: list[str] = []
    for key in person.prefs.get("presets", []):
        urls += [u for u in PRESETS[key][1] if u not in urls]
    urls += [u for u in person.prefs.get("feeds", []) if u not in urls]
    return urls


async def personal_news(c: Any, person: Person) -> list[dict[str, Any]]:
    """Новости из личных источников человека: берём на лету, свежие сверху."""
    urls = personal_feed_urls(person)
    results = await asyncio.gather(*(_cached_feed(c, u) for u in urls))
    limit = int(person.prefs.get("count", 5))
    rows: list[tuple[float, dict[str, Any]]] = []
    for url, res in zip(urls, results, strict=True):
        if res is None:
            continue
        title, items = res
        for it in items[:limit]:
            ts = it.published.timestamp() if it.published else 0.0
            rows.append(
                (
                    ts,
                    {
                        "title": it.title,
                        "text": (it.summary or "")[:240],
                        "link": it.link,
                        "feed": title or feed_name(url),
                        "sent_ts": int(ts),
                    },
                )
            )
    rows.sort(key=lambda r: r[0], reverse=True)
    return [r[1] for r in rows[: limit * 2]]


# ----------------------------------------------------------------------- админ: новости
def _admin_state(c: Any) -> dict[str, Any]:
    p = c.prefs.news()
    feeds = set(p.feeds)
    return {
        "prefs": {
            "enabled": p.enabled,
            "interval_min": p.interval_min,
            "count": p.count,
            "quiet": p.quiet,
            "quiet_from": p.quiet_from,
            "quiet_to": p.quiet_to,
            "retell": p.retell,
            "mode": p.mode,
        },
        "choices": {"interval": list(INTERVAL_CHOICES), "count": list(COUNT_CHOICES)},
        "presets": [_preset_json(k, feeds) for k in PRESET_KEYS],
        "feeds": [_feed_json(u) for u in p.feeds],
        "max": MAX_FEEDS,
    }


@routes.get("/api/settings/news")
async def news_get(request: web.Request) -> Any:
    return _admin_state(_ctx(request))


@routes.post("/api/settings/news")
async def news_post(request: web.Request) -> Any:
    c = _ctx(request)
    if c.prefs is None:
        raise ApiError("настройки недоступны", 503)
    body = await _json(request)
    op = body.get("op")
    p = c.prefs.news()
    result: dict[str, Any] = {}
    if op == "set":
        changes: dict[str, Any] = {}
        for key in ("enabled", "quiet", "retell"):
            if key in body:
                changes[key] = bool(body[key])
        if "interval_min" in body:
            if body["interval_min"] not in INTERVAL_CHOICES:
                raise ApiError("такой частоты нет")
            changes["interval_min"] = body["interval_min"]
        if "count" in body:
            if body["count"] not in COUNT_CHOICES:
                raise ApiError("такого количества нет")
            changes["count"] = body["count"]
        if "mode" in body:
            if body["mode"] not in (MODE_DAY, MODE_NEW):
                raise ApiError("такого режима нет")
            changes["mode"] = body["mode"]
        if changes:
            await c.prefs.update_news(**changes)
    elif op == "preset":
        key = str(_need(body.get("key"), "key"))
        if key not in PRESETS:
            raise ApiError("подборка не найдена")
        urls = PRESETS[key][1]
        current = list(p.feeds)
        if body.get("on"):
            todo = [u for u in urls if u not in current][: max(0, MAX_FEEDS - len(current))]
            checks = await asyncio.gather(*(c.news.check_feed(u) for u in todo))
            added = [u for u, (ok, _) in zip(todo, checks, strict=True) if ok]
            failed = [feed_name(u) for u, (ok, _) in zip(todo, checks, strict=True) if not ok]
            if added:
                await c.prefs.update_news(feeds=(*current, *added))
            result = {"added": len(added), "failed": failed}
        else:
            await c.prefs.update_news(feeds=tuple(u for u in current if u not in urls))
    elif op == "add":
        url = str(_need(body.get("url"), "url")).strip()
        if not url.startswith(("http://", "https://")):
            raise ApiError("нужна ссылка, которая начинается с http:// или https://")
        if url in p.feeds:
            raise ApiError("эта лента уже есть")
        if len(p.feeds) >= MAX_FEEDS:
            raise ApiError(f"лимит — {MAX_FEEDS} источников, удалите лишние")
        ok, info = await c.news.check_feed(url)
        if not ok:
            raise ApiError(f"не получилось: {info}")
        await c.prefs.update_news(feeds=(*p.feeds, url))
        result = {"name": info}
    elif op == "remove":
        url = str(_need(body.get("url"), "url"))
        await c.prefs.update_news(feeds=tuple(u for u in p.feeds if u != url))
    else:
        raise ApiError("неизвестное действие")
    return {**_admin_state(c), "result": result}


# ----------------------------------------------------------------------- гость: личные настройки
def _guest_state(person: Person) -> dict[str, Any]:
    chosen = set(person.prefs.get("presets", []))
    return {
        "nick": person.prefs.get("nick", ""),
        "count": person.prefs.get("count", 5),
        "count_choices": list(GUEST_COUNTS),
        "news": person.can("news"),
        "presets": [{"key": k, "label": PRESETS[k][0], "total": len(PRESETS[k][1]), "active": k in chosen} for k in PRESET_KEYS],
        "feeds": [_feed_json(u) for u in person.prefs.get("feeds", [])],
        "max": MAX_GUEST_FEEDS,
    }


@routes.get("/api/me/settings")
async def my_get(request: web.Request) -> Any:
    return _guest_state(_person(request))


def _person(request: web.Request) -> Person:
    person = request.get("guest")
    if person is None:
        raise ApiError("личные настройки есть только у гостей", 404)
    return person


@routes.post("/api/me/settings")
async def my_post(request: web.Request) -> Any:
    c = _ctx(request)
    person = _person(request)
    body = await _json(request)
    op = body.get("op")
    news_ops = {"preset", "add", "remove"}
    if op in news_ops and not person.can("news"):
        raise ApiError("новости вам не включены", 403)
    updated: Person | None = person
    if op == "set":
        changes: dict[str, Any] = {}
        if "nick" in body:
            changes["nick"] = str(body["nick"] or "").strip()[:32] or None
        if "count" in body:
            if body["count"] not in GUEST_COUNTS:
                raise ApiError("такого количества нет")
            changes["count"] = body["count"]
        updated = await c.access.set_prefs(person.id, **changes)
    elif op == "preset":
        key = str(_need(body.get("key"), "key"))
        if key not in PRESETS:
            raise ApiError("подборка не найдена")
        chosen = [k for k in person.prefs.get("presets", []) if k != key]
        updated = await c.access.set_prefs(person.id, presets=[*chosen, key] if body.get("on") else chosen)
    elif op == "add":
        feeds = list(person.prefs.get("feeds", []))
        if len(feeds) >= MAX_GUEST_FEEDS:
            raise ApiError(f"лимит — {MAX_GUEST_FEEDS} своих источников")
        try:
            url = await public_http_url(str(_need(body.get("url"), "url")))
        except ValueError as exc:
            raise ApiError(str(exc)) from exc
        if url in feeds:
            raise ApiError("эта лента уже есть")
        ok, _info = await c.news.check_feed(url)
        if not ok:
            raise ApiError("лента не открылась: проверьте ссылку")  # причину не раскрываем: гость не должен «прощупывать» сеть
        updated = await c.access.set_prefs(person.id, feeds=[*feeds, url])
    elif op == "remove":
        url = str(_need(body.get("url"), "url"))
        updated = await c.access.set_prefs(person.id, feeds=[u for u in person.prefs.get("feeds", []) if u != url])
    else:
        raise ApiError("неизвестное действие")
    return _guest_state(updated or person)
