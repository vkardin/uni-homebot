"""JSON API веб-панели: тонкий слой над теми же сервисами, что использует бот. Авторизация — подпись Telegram (initData)."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from homebot import __version__
from homebot.config import Settings
from homebot.services import backup as backup_svc
from homebot.services import tiles, usage
from homebot.services.apps import Apps
from homebot.services.apps_http import AppError
from homebot.services.db import Database
from homebot.services.expense_parse import parse_expense
from homebot.services.expenses import KEY_WORDS, Draft, guess_category, remember_category
from homebot.services.history import PERIODS, load_period, summarize
from homebot.services.news import feed_name
from homebot.services.p2p import HISTORY_KEY, chart_points
from homebot.services.p2p import stats as p2p_stats
from homebot.services.reminders import describe_rule, parse_recurring, parse_reminder
from homebot.services.sites import extract_urls, price_stats, set_target, target_of
from homebot.services.urlguard import public_http_url
from homebot.services.webtext import WebTextError, fetch_text
from homebot.web.auth import verify_init_user
from homebot.web.serialize import plain

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

Handler = Callable[[web.Request], Awaitable[Any]]


@dataclass(slots=True)
class Ctx:
    settings: Settings
    db: Database
    store: Any
    system: Any
    units: Any
    power: Any
    logs: Any
    apps: Apps
    tasks: Any
    ollama: Any
    news: Any
    network: Any
    prefs: Any = None
    access: Any = None  # AccessService: гости (принятые люди) входят в панель только в свои разделы
    converter: Any = None  # Converter: что умеет сервер для раздела «Инструменты»
    http: Any = None  # aiohttp.ClientSession: собрать заново сервисы включённого модуля
    inbox: Any = None  # Inbox: 🔔 центр уведомлений
    repair: Any = None  # Repair: 🛠 ремонт сервера по PIN


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _ctx(request: web.Request) -> Ctx:
    return request.app["ctx"]


async def _json(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise ApiError("ожидался JSON") from exc
    if not isinstance(data, dict):
        raise ApiError("ожидался объект JSON")
    return data


def _need(value: Any, name: str) -> Any:
    if value is None or value == "":
        raise ApiError(f"не хватает поля {name}")
    return value


def _service(value: Any, title: str) -> Any:
    if value is None:
        raise ApiError(f"{title} не подключён", 404)
    return value


async def _safe(coro: Awaitable[Any]) -> Any:
    """Для сводок: сбой одного приложения не ломает остальные."""
    try:
        return plain(await coro)
    except AppError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        log.debug("web: widget failed", exc_info=True)
        return {"error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------- middleware
@web.middleware
async def auth_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    if request.path.startswith("/api/"):
        ctx = _ctx(request)
        header = request.headers.get("Authorization", "")
        init = header[4:] if header.startswith("tma ") else ""
        user = verify_init_user(init, ctx.settings.bot_token.get_secret_value())
        if user is None:
            return web.json_response({"error": "нет доступа: откройте панель из Telegram"}, status=401)
        if user["id"] != ctx.settings.admin_id:
            person = ctx.access.get(user["id"]) if ctx.access is not None else None
            feature = _guest_feature(request.path)
            if person is None or person.status != "approved":
                return web.json_response({"error": "нет доступа: заявка не одобрена"}, status=401)
            if feature is None or (feature != "me" and feature not in person.perms):
                return web.json_response({"error": "эта функция вам не включена"}, status=403)
            request["guest"] = person
    try:
        response = await handler(request)
    except ApiError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)
    except AppError as exc:
        return web.json_response({"error": str(exc)}, status=502)
    except web.HTTPException:
        raise
    except Exception:
        log.exception("web: %s %s failed", request.method, request.path)
        return web.json_response({"error": "внутренняя ошибка, подробности в журнале"}, status=500)
    if isinstance(response, web.StreamResponse):
        if not request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"  # файлы панели: всегда сверять с сервером (304, если не менялись)
        return response
    return web.json_response(plain(response), dumps=lambda o: json.dumps(o, ensure_ascii=False))


# Что из API доступно гостям: путь → ключ функции (права выдаёт администратор). Всё остальное — только администратору.
_GUEST_PATHS = {
    "/api/me": "me",
    "/api/notes": "notes",
    "/api/reminders": "reminders",
    "/api/p2p": "p2p",
    "/api/news": "news",
    "/api/read": "news",
}


def _guest_feature(path: str) -> str | None:
    base = "/".join(path.split("/")[:3])  # /api/notes/5 → /api/notes
    return _GUEST_PATHS.get(base)


def _owner(request: web.Request) -> int:
    """0 — данные администратора, иначе ID гостя: гость видит и меняет только своё."""
    guest = request.get("guest")
    return int(guest.id) if guest is not None else 0


routes = web.RouteTableDef()


@routes.get("/api/me")
async def me(request: web.Request) -> Any:
    from homebot.services.access import FEATURES

    guest = request.get("guest")
    if guest is None:
        # плитки — чтобы панель сразу знала, какие вкладки и разделы показывать
        from homebot.services import modules

        c = _ctx(request)
        hidden = modules.hidden_panel(modules.off(c.store))
        lang = c.settings.language
        return {"role": "admin", "version": __version__, "tiles": tiles.describe(c.apps), "hidden": hidden, "lang": lang}
    return {
        "role": "guest",
        "name": guest.prefs.get("nick") or guest.name,
        "features": [k for k in FEATURES if k in guest.perms],
        "version": __version__,
    }


# ======================================================================= ГЛАВНАЯ
@routes.get("/api/home")
async def home(request: web.Request) -> Any:
    """Сводка для главного экрана: всё важное одним запросом; сбой одной части не ломает остальные."""
    c = _ctx(request)
    now = time.time()

    async def server() -> dict[str, Any]:
        snap, units = await asyncio.gather(c.system.snapshot(), c.units.status_all())
        sy = plain(snap)
        disks = sy.get("disks") or []
        temp = sy.get("temperature") or {}
        return {
            "cpu": sy.get("cpu_percent"),
            "ram": sy.get("ram_percent"),
            "disk": max((d["percent"] for d in disks), default=None),
            "temp": temp.get("current"),
            "failed": [u.name for u in units if getattr(u, "active_state", "") == "failed"],
        }

    async def rate() -> dict[str, Any] | None:
        if c.apps.p2p is None:
            return None
        quote = await c.apps.p2p.quote()
        points = chart_points(c.store.get(HISTORY_KEY), now=now)[-48:]
        return {
            "best": quote.best,
            "typical": quote.typical,
            "points": [p[2] for p in points],
            "fiat": c.apps.p2p.fiat,
            "asset": c.apps.p2p.asset,
        }

    async def money() -> dict[str, Any] | None:
        if c.apps.actual is None:
            return None
        month = await c.apps.actual.month(datetime.now(c.settings.tz).date())
        return {"spent": month.spent, "earned": month.earned, "currency": c.settings.actual_currency}

    async def downloads() -> dict[str, Any]:
        out: dict[str, Any] = {"movies": 0, "shows": 0}
        if c.apps.radarr:
            out["movies"] = len(await _queue(c.apps.radarr))
        if c.apps.sonarr:
            out["shows"] = len(await _queue(c.apps.sonarr))
        return out

    async def sites() -> dict[str, Any] | None:
        if c.apps.sites is None:
            return None
        items, total = await c.apps.sites.sites(limit=50)
        drops = [x for x in items if x.price is not None and x.last_price is not None and x.price < x.last_price]
        return {
            "total": total,
            "problems": sum(1 for x in items if x.error),
            "drops": [{"name": x.name, "price": x.price, "was": x.last_price} for x in drops[:3]],
            "currency": c.settings.sites_currency,
        }

    reminders = await c.db.pending_reminders(4)
    recent = await c.db.recent_sent_news(3)
    parts = await asyncio.gather(_safe(server()), _safe(rate()), _safe(money()), _safe(downloads()), _safe(sites()))
    return {
        "host": socket.gethostname(),
        "version": __version__,
        "server": parts[0],
        "rate": parts[1],
        "money": parts[2],
        "downloads": parts[3],
        "sites": parts[4],
        "reminders": [{**plain(i), "rule_text": describe_rule(i.repeat) if i.repeat else ""} for i in reminders],
        "news": [{"title": n.title, "feed": feed_name(n.feed), "link": n.link, "sent_ts": n.sent_ts} for n in recent],
    }


# ======================================================================= СЕРВЕР
@routes.get("/api/overview")
async def overview(request: web.Request) -> Any:
    c = _ctx(request)
    snap, units, rows = await asyncio.gather(
        c.system.snapshot(), _safe(c.units.status_all()), load_period(c.db, "6h"), return_exceptions=True
    )
    return {
        "version": __version__,
        "host": socket.gethostname(),
        "system": snap if not isinstance(snap, Exception) else None,
        "services": units,
        "spark": [{"ts": r.ts, "cpu": r.cpu, "ram": r.ram, "temp": r.temp} for r in (rows if not isinstance(rows, Exception) else [])],
        "tasks": c.tasks.items(),
        "connected": c.apps.available(),
        "tiles": tiles.describe(c.apps),
        "power_enabled": bool(c.power.power_enabled),
        "restartable": list(c.power.restartable),
    }


@routes.get("/api/history")
async def history(request: web.Request) -> Any:
    c = _ctx(request)
    period = request.query.get("period", "24h")
    if period not in PERIODS:
        raise ApiError("неизвестный период")
    rows = await load_period(c.db, period)
    step = max(1, len(rows) // 400)  # график не нуждается в тысячах точек
    return {"rows": rows[::step], "stats": summarize(rows), "periods": list(PERIODS)}


@routes.get("/api/top")
async def top(request: web.Request) -> Any:
    return await _ctx(request).system.top_processes(10)


@routes.post("/api/services/restart")
async def restart_service(request: web.Request) -> Any:
    c = _ctx(request)
    unit = str(_need((await _json(request)).get("unit"), "unit"))
    if unit not in c.power.restartable:
        raise ApiError("эту службу перезапускать нельзя", 403)
    return await c.power.restart(unit)


@routes.post("/api/power")
async def power(request: web.Request) -> Any:
    c = _ctx(request)
    data = await _json(request)
    action = data.get("action")
    if action not in ("reboot", "poweroff") or data.get("confirm") is not True:
        raise ApiError("нужны action=reboot|poweroff и confirm=true")
    return await (c.power.reboot() if action == "reboot" else c.power.poweroff())


@routes.get("/api/logs")
async def logs(request: web.Request) -> Any:
    c = _ctx(request)
    unit = request.query.get("unit", "")
    if not unit:
        return {"units": list(c.logs.units)}
    if unit not in c.logs.units:
        raise ApiError("неизвестная служба", 404)
    lines = min(max(int(request.query.get("lines", "60") or 60), 10), 300)
    text, error = await c.logs.tail(unit, lines)
    return {"units": list(c.logs.units), "text": text, "error": error}


@routes.get("/api/docker")
async def docker(request: web.Request) -> Any:
    docker_svc = _service(_ctx(request).apps.docker, "Docker")
    containers = await docker_svc.containers()
    return [{**plain(item), "emoji": item.emoji, "problem": item.problem, "health": item.health} for item in containers]


@routes.post("/api/docker/restart")
async def docker_restart(request: web.Request) -> Any:
    docker_svc = _service(_ctx(request).apps.docker, "Docker")
    ok, message = await docker_svc.restart(str(_need((await _json(request)).get("name"), "name")))
    return {"ok": ok, "message": message}


@routes.get("/api/docker/logs")
async def docker_logs(request: web.Request) -> Any:
    docker_svc = _service(_ctx(request).apps.docker, "Docker")
    return {"text": await docker_svc.logs(request.query.get("name", ""), 80)}


@routes.get("/api/apps")
async def apps_summary(request: web.Request) -> Any:
    a = _ctx(request).apps
    out: dict[str, Any] = {}
    if a.qbit:
        out["qbit"] = await _safe(_qbit(a.qbit))
    if a.adguard:
        out["adguard"] = await _safe(a.adguard.stats())
    if a.speedtest:
        out["speedtest"] = await _safe(a.speedtest.latest())
    if a.jellyfin:
        out["jellyfin"] = await _safe(_jellyfin(a.jellyfin))
    if shutil.which("tailscale"):  # не установлен — карточку не показываем, а не пугаем ошибкой
        out["tailscale"] = await _safe(_ctx(request).network.tailscale())
    return out


async def _qbit(service: Any) -> dict[str, Any]:
    transfer, torrents = await asyncio.gather(service.transfer(), service.torrents())
    return {"transfer": transfer, "torrents": [{**plain(t), "done": t.done} for t in torrents[:30]]}


async def _jellyfin(service: Any) -> dict[str, Any]:
    playing, recent = await asyncio.gather(service.now_playing(), service.recently_added(12))
    return {"now_playing": playing, "recent": recent}


@routes.post("/api/qbit/toggle")
async def qbit_toggle(request: web.Request) -> Any:
    q = _service(_ctx(request).apps.qbit, "qBittorrent")
    if (await _json(request)).get("pause"):
        await q.pause_all()
    else:
        await q.resume_all()
    return {"ok": True}


@routes.post("/api/qbit/magnet")
async def qbit_magnet(request: web.Request) -> Any:
    q = _service(_ctx(request).apps.qbit, "qBittorrent")
    magnet = str(_need((await _json(request)).get("magnet"), "magnet")).strip()
    if not magnet.lower().startswith("magnet:?"):
        raise ApiError("это не magnet-ссылка")
    await q.add_magnet(magnet)
    return {"ok": True}


@routes.post("/api/adguard/protection")
async def adguard_protection(request: web.Request) -> Any:
    a = _service(_ctx(request).apps.adguard, "AdGuard")
    data = await _json(request)
    await a.set_protection(bool(data.get("enabled")), minutes=int(data.get("minutes") or 0))
    return {"ok": True}


@routes.post("/api/speedtest/run")
async def speedtest_run(request: web.Request) -> Any:
    await _service(_ctx(request).apps.speedtest, "Speedtest").run()
    return {"ok": True}


# ======================================================================= САЙТЫ
def _site_json(site: Any, c: Ctx) -> dict[str, Any]:
    return {
        **plain(site),
        "name": site.name,
        "is_price": site.is_price,
        "blocked": site.blocked,
        "target": target_of(c.store, site.uuid),
    }


@routes.get("/api/sites")
async def sites_list(request: web.Request) -> Any:
    c = _ctx(request)
    service = _service(c.apps.sites, "changedetection")
    items, total = await service.sites(limit=50)
    return {"sites": [_site_json(s, c) for s in items], "total": total, "currency": c.settings.sites_currency}


@routes.get("/api/sites/{uuid}")
async def site_detail(request: web.Request) -> Any:
    c = _ctx(request)
    service = _service(c.apps.sites, "changedetection")
    uuid = request.match_info["uuid"]
    site = await service.details(uuid)
    series: list[tuple[int, float]] = []
    if site.is_price:
        try:
            series = await service.price_history(uuid)
        except AppError:
            series = []
    return {
        "site": _site_json(site, c),
        "series": series,
        "stats": price_stats(series) if series else None,
        "currency": c.settings.sites_currency,
    }


@routes.post("/api/sites")
async def site_add(request: web.Request) -> Any:
    c = _ctx(request)
    service = _service(c.apps.sites, "changedetection")
    data = await _json(request)
    urls = extract_urls(str(data.get("urls", "")))
    if not urls:
        raise ApiError("не вижу ссылок")
    added, failed = [], []
    for url in urls:
        try:
            uuid = await service.add(
                url, price=data.get("mode") != "page", browser=bool(data.get("browser", c.settings.sites_browser))
            )
            await service.recheck(uuid)
            added.append(uuid)
        except AppError as exc:
            failed.append({"url": url, "error": str(exc)})
    return {"added": added, "failed": failed}


@routes.post("/api/sites/{uuid}/{action}")
async def site_action(request: web.Request) -> Any:
    c = _ctx(request)
    service = _service(c.apps.sites, "changedetection")
    uuid, action = request.match_info["uuid"], request.match_info["action"]
    data = await _json(request) if request.can_read_body else {}
    if action == "check":
        await service.recheck(uuid)
    elif action == "pause":
        await service.set_paused(uuid, bool(data.get("paused", True)))
    elif action == "interval":
        await service.set_interval(uuid, int(data.get("minutes") or 0))
    elif action == "browser":
        await service.set_browser(uuid, bool(data.get("on")))
        await service.recheck(uuid)
    elif action == "target":
        raw = data.get("price")
        await set_target(c.store, uuid, float(raw) if raw not in (None, "") else None)
    else:
        raise ApiError("неизвестное действие", 404)
    return {"ok": True}


@routes.delete("/api/sites/{uuid}")
async def site_delete(request: web.Request) -> Any:
    c = _ctx(request)
    await _service(c.apps.sites, "changedetection").delete(request.match_info["uuid"])
    await set_target(c.store, request.match_info["uuid"], None)
    return {"ok": True}


# ======================================================================= КИНО
@routes.get("/api/movies")
async def movies(request: web.Request) -> Any:
    a = _ctx(request).apps
    out: dict[str, Any] = {"radarr": a.radarr is not None, "sonarr": a.sonarr is not None}
    if a.radarr:
        out["movie_queue"] = await _safe(_queue(a.radarr))
    if a.sonarr:
        out["show_queue"] = await _safe(_queue(a.sonarr))
    if a.jellyfin:
        out["recent"] = await _safe(a.jellyfin.recently_added(12))
    return out


async def _queue(service: Any) -> list[Any]:
    return list((await service.queue()).values())


@routes.get("/api/movies/search")
async def movies_search(request: web.Request) -> Any:
    a = _ctx(request).apps
    term = request.query.get("q", "").strip()
    if len(term) < 2:
        raise ApiError("слишком короткий запрос")
    films, shows = await asyncio.gather(
        _safe(a.radarr.lookup(term)) if a.radarr else _none(),
        _safe(a.sonarr.lookup(term)) if a.sonarr else _none(),
    )
    return {"movies": films, "shows": shows}


async def _none() -> list[Any]:
    return []


@routes.post("/api/movies/add")
async def movies_add(request: web.Request) -> Any:
    a = _ctx(request).apps
    data = await _json(request)
    ident = int(_need(data.get("id"), "id"))
    if data.get("kind") == "show":
        sonarr = _service(a.sonarr, "Sonarr")
        show = await sonarr.add(ident)
        if show.sonarr_id:
            await sonarr.search(show.sonarr_id)
        return {"title": show.title}
    radarr = _service(a.radarr, "Radarr")
    film = await radarr.add(ident)
    if film.radarr_id:
        await radarr.search(film.radarr_id)
    return {"title": film.title}


# ======================================================================= ФИНАНСЫ / P2P
@routes.get("/api/finance")
async def finance(request: web.Request) -> Any:
    c = _ctx(request)
    actual = _service(c.apps.actual, "Actual Budget")
    today = datetime.now(c.settings.tz).date()
    accounts, month, categories = await asyncio.gather(actual.accounts(balances=True), actual.month(today), actual.categories())
    names = {x.id: x.name for x in categories}
    recent = await actual.recent([a for a in accounts if not a.closed], today - timedelta(days=30), 15)
    return {
        "currency": c.settings.actual_currency,
        "accounts": [a for a in accounts if not a.closed],
        "month": month,
        "recent": [{**plain(t), "category": names.get(t.category_id, "")} for t in recent],
    }


@routes.post("/api/finance/expense")
async def finance_expense(request: web.Request) -> Any:
    c = _ctx(request)
    actual = _service(c.apps.actual, "Actual Budget")
    parsed = parse_expense(str(_need((await _json(request)).get("text"), "text")))
    if parsed is None:
        raise ApiError("не нашёл сумму: напишите, например, «кофе 85»")
    categories = await actual.categories()
    guess = guess_category(parsed.note, categories, dict(c.store.get(KEY_WORDS) or {}), income=parsed.is_income)
    account = await actual.account()
    draft = Draft(0, parsed, datetime.now(c.settings.tz).date().isoformat(), category_id=guess, account_id=account.id)
    await actual.add(draft.to_txn(), draft.account_id or None)
    if guess:
        await remember_category(c.store, parsed.note, guess)
    return {"cents": parsed.cents, "note": parsed.note, "category": next((x.name for x in categories if x.id == guess), "")}


@routes.get("/api/p2p")
async def p2p(request: web.Request) -> Any:
    c = _ctx(request)
    service = _service(c.apps.p2p, "P2P")
    quote = await service.quote()
    now = time.time()
    return {
        "quote": quote,
        "stats": p2p_stats(c.store.get(HISTORY_KEY), now=now),
        "points": chart_points(c.store.get(HISTORY_KEY), now=now),
        "fiat": service.fiat,
        "asset": service.asset,
    }


# ======================================================================= НОВОСТИ / ЧАТ / НАПОМИНАНИЯ
@routes.get("/api/news")
async def news(request: web.Request) -> Any:
    guest = request.get("guest")
    if guest is not None and (guest.prefs.get("presets") or guest.prefs.get("feeds")):
        from homebot.web.settings_api import personal_news  # свои источники человека, а не общая лента хозяина

        return await personal_news(_ctx(request), guest)
    items = await _ctx(request).db.recent_sent_news(30)
    return [{"title": n.title, "text": n.text, "link": n.link, "feed": feed_name(n.feed), "sent_ts": n.sent_ts} for n in items]


@routes.get("/api/read")
async def read(request: web.Request) -> Any:
    """📖 Режим чтения: текст статьи по ссылке (открыть сайт прямо в панели нельзя — сайты запрещают встраивание)."""
    try:
        url = await public_http_url(request.query.get("url", ""))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    async with aiohttp.ClientSession(headers={"User-Agent": f"homebot/{__version__}"}) as session:
        try:
            title, text = await fetch_text(session, url)
        except WebTextError as exc:
            raise ApiError(str(exc), status=502) from exc
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    return {"url": url, "title": title or "", "paragraphs": paragraphs[:200]}


@routes.post("/api/chat")
async def chat(request: web.Request) -> Any:
    c = _ctx(request)
    messages = (await _json(request)).get("messages")
    if not isinstance(messages, list) or not messages:
        raise ApiError("нет сообщений")
    history = [
        {"role": m["role"], "content": str(m["content"])[:4000]}
        for m in messages[-20:]
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
    ]
    if not history:
        raise ApiError("нет сообщений")
    return {"reply": await c.ollama.chat(history), "model": c.ollama.active_model}


@routes.get("/api/reminders")
async def reminders_list(request: web.Request) -> Any:
    items = await _ctx(request).db.pending_reminders(50, owner=_owner(request))
    return [{**plain(i), "rule_text": describe_rule(i.repeat) if i.repeat else ""} for i in items]


@routes.post("/api/reminders")
async def reminders_add(request: web.Request) -> Any:
    c = _ctx(request)
    text = str(_need((await _json(request)).get("text"), "text"))
    now = datetime.now(c.settings.tz)
    parsed = parse_recurring(text, now) or parse_reminder(text, now)
    if parsed is None:
        raise ApiError("не понял, когда напомнить (например: «завтра 9:00 оплатить интернет»)")
    owner = _owner(request)
    if owner and await c.db.count_reminders(owner) >= 30:
        raise ApiError("достигнут лимит напоминаний (30)")
    await c.db.add_reminder(int(parsed.due.timestamp()), parsed.text[:500], parsed.rule, owner=owner)
    return {"due": int(parsed.due.timestamp()), "text": parsed.text, "repeat": parsed.rule}


@routes.delete("/api/reminders/{id}")
async def reminders_delete(request: web.Request) -> Any:
    return {"ok": await _ctx(request).db.delete_reminder(int(request.match_info["id"]), owner=_owner(request))}


@routes.get("/api/notes")
async def notes_list(request: web.Request) -> Any:
    return await _ctx(request).db.list_notes(50, owner=_owner(request))


@routes.post("/api/notes")
async def notes_add(request: web.Request) -> Any:
    text = str(_need((await _json(request)).get("text"), "text")).strip()[:2000]
    owner = _owner(request)
    if owner and await _ctx(request).db.count_notes(owner) >= 100:
        raise ApiError("достигнут лимит заметок (100)")
    await _ctx(request).db.add_note(int(time.time()), text, owner=owner)
    return {"ok": True}


@routes.delete("/api/notes/{id}")
async def notes_delete(request: web.Request) -> Any:
    return {"ok": await _ctx(request).db.delete_note(int(request.match_info["id"]), owner=_owner(request))}


# ======================================================================= НАСТРОЙКИ
@routes.post("/api/tasks/toggle")
async def tasks_toggle(request: web.Request) -> Any:
    c = _ctx(request)
    name = str(_need((await _json(request)).get("name"), "name"))
    try:
        running = await c.tasks.toggle(name)
    except KeyError as exc:
        raise ApiError("нет такой задачи", 404) from exc
    return {"running": running}


@routes.get("/api/backup")
async def backup_info(request: web.Request) -> Any:
    c = _ctx(request)
    return {"items": backup_svc.list_backups(c.settings.state_dir), "status": backup_svc.last_status(c.store)}


@routes.post("/api/backup/now")
async def backup_now(request: web.Request) -> Any:
    c = _ctx(request)
    try:
        item = await backup_svc.run_backup(
            c.settings.state_dir, c.db, c.store, keep=c.settings.backup_keep,
            extra=backup_svc.parse_extra_paths(c.settings.backup_paths), tz=c.settings.tz,
        )  # fmt: skip
    except backup_svc.BackupError as exc:
        raise ApiError(str(exc), 500) from exc
    return item


@routes.get("/api/usage")
async def usage_info(request: web.Request) -> Any:
    return [{"name": n, "label": usage.LABELS.get(n, n), "count": v} for n, v in usage.top(_ctx(request).store, 20)]


# ======================================================================= ИНСТРУМЕНТЫ
@routes.get("/api/tools")
async def tools_info(request: web.Request) -> Any:
    from homebot.services.convert import KIND_TITLE, KINDS, describe_caps, install_hint, ops_for

    conv = _ctx(request).converter
    if conv is None:
        raise ApiError("инструменты недоступны", 503)
    caps = conv.refresh()
    return {
        "caps": [{"ok": ok, "name": name, "what": what} for ok, name, what in describe_caps(caps)],
        "hint": install_hint(caps),
        "kinds": [{"title": KIND_TITLE[k], "ops": [o.label for o in ops_for(k, caps, "x.heic" if caps.heif else "x")]} for k in KINDS],
        "download": caps.ytdlp,
    }


# ======================================================================= 🧱 модули и самовосстановление
def _modules_state(c: Ctx) -> dict[str, Any]:
    from homebot.services import modules

    off = modules.off(c.store)
    items = []
    for tid, _ in modules.rows(c.apps, off):
        kind = "section" if tid in modules.FEATURE_BY_ID else "app"
        items.append({"id": tid, "title": modules.title(tid), "on": tid not in off, "kind": kind})
    return {"items": items, "broken": [{"title": t, "error": e} for t, e in c.tasks.broken()], "hidden": modules.hidden_panel(off)}


@routes.get("/api/modules")
async def modules_get(request: web.Request) -> Any:
    return _modules_state(_ctx(request))


@routes.post("/api/modules")
async def modules_post(request: web.Request) -> Any:
    from homebot.services import connect, modules
    from homebot.services.apps import build_apps
    from homebot.ui import keyboards as kb

    c = _ctx(request)
    body = await _json(request)
    feature = modules.FEATURE_BY_ID.get(str(body.get("id", "")))
    if feature is not None:
        from homebot.app import sync_modules

        await modules.set_off(c.store, feature.id, not body.get("on"))
        sync_modules(c.store)  # меню в чате; подсказки команд обновятся при перезапуске
        return _modules_state(c)
    tile = tiles.BY_ID.get(str(body.get("id", "")))
    if tile is None:
        raise ApiError("такого модуля нет", 404)
    on = bool(body.get("on"))
    if on and tile.id in modules.off(c.store):
        if c.http is None:
            raise ApiError("включение недоступно", 503)
        await modules.set_off(c.store, tile.id, False)
        modules.restore(c.apps, build_apps(c.http, connect.apply(c.settings, connect.load(c.settings.state_dir))), tile.id)
    elif not on and tile.connected(c.apps):
        await modules.set_off(c.store, tile.id, True)
        modules.apply(c.apps, {tile.id})
    kb.set_connected_tiles(tiles.connected_ids(c.apps))  # меню в чате; подсказки команд обновятся при перезапуске
    return _modules_state(c)


@routes.post("/api/repair")
async def repair(request: web.Request) -> Any:
    c = _ctx(request)
    return {"count": c.tasks.repair(), **_modules_state(c)}


# ======================================================================= 🔔 центр уведомлений
@routes.get("/api/inbox")
async def inbox_get(request: web.Request) -> Any:
    inbox = _ctx(request).inbox
    if inbox is None:
        return {"items": [], "unread": 0}
    return {"items": inbox.items(), "unread": inbox.unread()}


@routes.post("/api/inbox")
async def inbox_post(request: web.Request) -> Any:
    inbox = _ctx(request).inbox
    if inbox is None:
        raise ApiError("центр уведомлений недоступен", 503)
    op = (await _json(request)).get("op")
    if op == "read":
        inbox.mark_read()
    elif op == "clear":
        inbox.clear()
    else:
        raise ApiError("неизвестное действие")
    return {"items": inbox.items(), "unread": inbox.unread()}


# ======================================================================= 🛠 ремонт сервера
def _repair_state(c: Ctx) -> dict[str, Any]:
    r = c.repair
    if r is None or not r.enabled:
        return {"enabled": False, "actions": [], "history": [], "locked": 0}
    actions = [{"id": a.id, "title": a.title, "danger": a.danger} for a in r.actions()]
    return {"enabled": True, "actions": actions, "history": r.history(10), "locked": r.locked()}


@routes.get("/api/server-repair")
async def server_repair_get(request: web.Request) -> Any:
    return _repair_state(_ctx(request))


@routes.post("/api/server-repair")
async def server_repair_post(request: web.Request) -> Any:
    c = _ctx(request)
    if c.repair is None or not c.repair.enabled:
        raise ApiError("ремонт сервера выключен", 403)
    body = await _json(request)
    ok, detail = await c.repair.run(str(body.get("action", "")), str(body.get("pin", "")), c.settings.admin_id)
    return {"ok": ok, "detail": detail, **_repair_state(c)}


# ======================================================================= 🚀 доска приложений
def _launcher_state(c: Ctx) -> dict[str, Any]:
    from homebot.services import launcher

    connected = {name for name in ("radarr", "sonarr", "jellyfin", "qbit", "adguard", "speedtest", "sites", "actual")
                 if getattr(c.apps, name, None) is not None}  # fmt: skip
    return {"apps": launcher.apps(c.settings, connected), "links": launcher.links(c.store)}


@routes.get("/api/launcher")
async def launcher_get(request: web.Request) -> Any:
    return _launcher_state(_ctx(request))


@routes.post("/api/launcher")
async def launcher_post(request: web.Request) -> Any:
    from homebot.services import launcher

    c = _ctx(request)
    body = await _json(request)
    items = launcher.links(c.store)
    op = body.get("op")
    if op == "add":
        link = launcher.clean_link(
            str(body.get("title", "")), str(body.get("url", "")), str(body.get("icon", "")), str(body.get("group", ""))
        )
        if link is None:
            raise ApiError("нужны название и ссылка http(s)://")
        if len(items) >= launcher.MAX_LINKS:
            raise ApiError(f"не больше {launcher.MAX_LINKS} ссылок")
        items.append(link)
    elif op == "remove":
        items = [x for x in items if x.get("url") != body.get("url")]
    elif op == "move":
        items = launcher.move(items, str(body.get("url", "")), -1 if int(body.get("step", 1)) < 0 else 1)
    elif op == "group":
        items = launcher.regroup(items, str(body.get("url", "")), str(body.get("group", "")))
    else:
        raise ApiError("неизвестное действие")
    await c.store.set(launcher.KEY, items)
    return _launcher_state(c)


@routes.get("/api/launcher/status")
async def launcher_status(request: web.Request) -> Any:
    from homebot.services import launcher

    c = _ctx(request)
    if c.http is None:
        return {}
    st = _launcher_state(c)
    probes = {x["id"]: x["probe"] for x in st["apps"]} | {x["url"]: x["url"] for x in st["links"]}
    alive = await launcher.status(c.http, list(probes.values()))
    return {key: alive.get(url) for key, url in probes.items()}


# ======================================================================= 🧭 начало работы
def _setup_state(c: Ctx) -> dict[str, Any]:
    from homebot.services import setup

    feeds = c.prefs.news().feeds if c.prefs is not None else ()
    return setup.as_json(setup.steps(c.settings, c.apps, feeds), c.store)


@routes.get("/api/setup")
async def setup_get(request: web.Request) -> Any:
    return _setup_state(_ctx(request))


@routes.post("/api/setup")
async def setup_post(request: web.Request) -> Any:
    from homebot.services import setup

    c = _ctx(request)
    await setup.set_hidden(c.store, bool((await _json(request)).get("hidden")))
    return _setup_state(c)


# ======================================================================= ⚙️ общие настройки (settings.json)
def _general_state(c: Ctx) -> dict[str, Any]:
    from homebot.services import overrides

    data = overrides.load(c.settings.state_dir)
    saved = c.store.get("overrides_pending", False)
    return {"items": overrides.describe(c.settings, data), "pending": bool(saved)}


@routes.get("/api/settings/general")
async def general_get(request: web.Request) -> Any:
    return _general_state(_ctx(request))


@routes.post("/api/settings/general")
async def general_post(request: web.Request) -> Any:
    import os
    import signal

    from homebot.services import overrides

    c = _ctx(request)
    body = await _json(request)
    op = body.get("op")
    if op == "restart":
        await c.store.set("overrides_pending", False)
        # как «Перезапустить бота» в ремонте: SIGTERM через секунду, systemd или Docker поднимут бота снова
        asyncio.get_running_loop().call_later(1.0, os.kill, os.getpid(), signal.SIGTERM)
        return {"ok": True}
    name = str(body.get("name", ""))
    if name in c.settings.model_fields_set:
        raise ApiError("задано в .env на сервере — меняется только там")
    data = overrides.load(c.settings.state_dir)
    if op == "set":
        try:
            data[name] = overrides.validate(name, body.get("value"))
        except ValueError as exc:
            raise ApiError(str(exc)) from None
    elif op == "reset":
        data.pop(name, None)
    else:
        raise ApiError("неизвестное действие")
    overrides.save(c.settings.state_dir, data)
    await c.store.set("overrides_pending", True)
    return _general_state(c)


# ======================================================================= страница
async def index(request: web.Request) -> web.StreamResponse:
    # Версия в адресах стилей и скрипта: после обновления Telegram не покажет старые файлы из кэша
    html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__V__", __version__)
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


def build_app(ctx: Ctx) -> web.Application:
    app = web.Application(middlewares=[auth_middleware], client_max_size=64 * 1024)
    app["ctx"] = ctx
    from homebot.web.settings_api import routes as settings_routes

    app.add_routes(routes)
    app.add_routes(settings_routes)
    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC, follow_symlinks=False)
    return app
