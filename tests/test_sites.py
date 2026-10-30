"""🔎 Сайты (changedetection.io): разбор снимков и ссылок, клиент API, фоновый опрос изменений, тексты."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from aiogram.exceptions import TelegramAPIError

from homebot.jobs.sites_watch import ERROR_AFTER, SitesWatchJob
from homebot.services.apps_http import AppError
from homebot.services.sites import (
    KEY_TARGETS,
    STATE_KEY,
    Change,
    Site,
    SitesService,
    check_target,
    describe_change,
    diff_lines,
    domain,
    extract_urls,
    interval_body,
    normalize_url,
    parse_site,
    parse_snapshot,
    set_target,
    target_of,
)
from homebot.services.state import StateStore
from homebot.ui.sites_texts import ago, every, money, render_site_card, render_site_change, render_site_error, render_sites
from tests.fakes import FakeResponse, FakeSession

NOW = 1_800_000_000.0


# --------------------------------------------------------------------------- чистая логика
def test_normalize_url():
    assert normalize_url("https://www.olx.ua/d/uk/obyavlenie/x-IDabc.html") == "https://www.olx.ua/d/uk/obyavlenie/x-IDabc.html"
    assert normalize_url("глянь https://rozetka.com.ua/ua/p123/p123/, дорого") == "https://rozetka.com.ua/ua/p123/p123/"
    assert normalize_url("olx.ua/d/x") == "https://olx.ua/d/x"
    assert normalize_url("(https://shop.example.com/a?b=1)") == "https://shop.example.com/a?b=1"
    assert normalize_url("привет") is None
    assert normalize_url("") is None
    assert normalize_url("ftp://example.com/x") is None
    assert normalize_url("http://localhost/x") is None  # без точки в имени — не сайт
    assert domain("https://www.olx.ua/d/x") == "olx.ua"


def test_parse_snapshot_price_and_page():
    assert parse_snapshot("In Stock: True - Price: 129.99") == (True, 129.99)
    assert parse_snapshot("In Stock: False - Price: 1200.0") == (False, 1200.0)
    assert parse_snapshot("In Stock: True - Price: ") == (True, None)
    assert parse_snapshot("In Stock: None - Price: 5,5") == (None, 5.5)
    assert parse_snapshot("Просто текст страницы\nЦена 100") is None


def test_diff_lines_added_removed_and_limits():
    old = "Объявление 1\nОбъявление 2\nОбъявление 3\n"
    new = "Объявление 0\nОбъявление 1\nОбъявление 3\n\n  Объявление   4  \n"
    added, removed = diff_lines(old, new)
    assert added == ("Объявление 0", "Объявление 4") and removed == ("Объявление 2",)
    many = "\n".join(f"строка {n}" for n in range(30))
    added, removed = diff_lines("", many, limit=5)
    assert len(added) == 5 and removed == ()
    long_added, _ = diff_lines("", "x" * 500, width=50)
    assert len(long_added[0]) == 51 and long_added[0].endswith("…")


def test_describe_change_kinds():
    price = describe_change("In Stock: True - Price: 1400.0", "In Stock: True - Price: 1200.0")
    assert price.kind == "price" and price.old_price == 1400.0 and price.new_price == 1200.0
    page = describe_change("a\nb", "a\nc")
    assert page.kind == "page" and page.added == ("c",) and page.removed == ("b",)


def test_parse_site_modes_interval_and_errors():
    raw = {
        "url": "https://olx.ua/d/x",
        "title": "Велосипед",
        "processor": "restock_diff",
        "paused": True,
        "last_checked": 1_799_999_000,
        "last_changed": 1_799_000_000,
        "last_error": "Error - 403 Forbidden",
        "time_between_check_use_default": False,
        "time_between_check": {"weeks": 0, "days": 0, "hours": 3, "minutes": 30, "seconds": 0},
        "restock": {"price": 1200.0, "last_price": 1400, "in_stock": True},
    }
    site = parse_site("u1", raw)
    assert site.is_price and site.paused and site.interval_min == 210 and site.blocked
    assert (site.price, site.last_price, site.in_stock) == (1200.0, 1400.0, True)
    plain = parse_site("u2", {"url": "https://a.b/c", "page_title": "Заголовок", "processor": "text_json_diff", "last_error": False})
    assert not plain.is_price and plain.name == "Заголовок" and plain.error == "" and plain.interval_min == 0
    bare = parse_site("u3", {"url": "https://shop.example.com/x"})
    assert bare.mode == "" and bare.name == "shop.example.com"


def test_interval_body():
    assert interval_body(0) == {"time_between_check_use_default": True}
    body = interval_body(1500)
    assert body["time_between_check_use_default"] is False
    assert body["time_between_check"] == {"weeks": 0, "days": 1, "hours": 1, "minutes": 0, "seconds": 0}


# ------------------------------------------------------------------------------ клиент API
WATCHES = {
    "aaaa-1": {"url": "https://olx.ua/d/1", "title": "Велосипед", "last_changed": 100, "last_checked": 200, "last_error": False},
    "bbbb-2": {"url": "https://rozetka.com.ua/p2", "title": "", "last_changed": 0, "last_checked": 0, "last_error": "Error - 403"},
}


def _service(handler):
    session = FakeSession(handler)
    return SitesService(session, "http://127.0.0.1:5000/", "KEY"), session  # type: ignore[arg-type]


def test_service_sends_key_and_lists():
    service, session = _service(lambda m, u, kw: FakeResponse(body=WATCHES))
    sites = asyncio.run(service.brief())
    assert {s.uuid for s in sites} == {"aaaa-1", "bbbb-2"}
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("GET", "http://127.0.0.1:5000/api/v1/watch") and kwargs["headers"] == {"x-api-key": "KEY"}
    assert asyncio.run(service.check()) == 2


def test_service_auth_error_is_readable():
    service, _ = _service(lambda m, u, kw: FakeResponse(status=403, body={"message": "Invalid API key"}))
    try:
        asyncio.run(service.brief())
    except AppError as exc:
        assert "ключ" in str(exc) or "доступа" in str(exc)
    else:
        raise AssertionError("ожидалась AppError")


def test_add_uses_processor_and_falls_back_to_lookup():
    created: list[dict] = []

    def handler(method, url, kw):
        if method == "POST":
            created.append(kw["json"])
            return FakeResponse(raw=b'"OK"')  # старые версии: без uuid
        return FakeResponse(body={"new-1": {"url": "https://olx.ua/d/9", "title": ""}})

    service, _ = _service(handler)
    uuid = asyncio.run(service.add("https://olx.ua/d/9", price=True))
    assert uuid == "new-1" and created[0] == {"url": "https://olx.ua/d/9", "processor": "restock_diff"}
    asyncio.run(service.add("https://olx.ua/d/9", price=False))
    assert created[1]["processor"] == "text_json_diff"

    service2, _ = _service(lambda m, u, kw: FakeResponse(body={"uuid": "from-api"}) if m == "POST" else FakeResponse(body={}))
    assert asyncio.run(service2.add("https://x.y/z", price=False)) == "from-api"


def test_control_calls_accept_plain_text_answers():
    seen: list[tuple[str, str, dict]] = []

    def handler(method, url, kw):
        seen.append((method, url, kw.get("params") or {}))
        return FakeResponse(raw=b"OK")

    service, _ = _service(handler)

    async def scenario():
        await service.recheck("u1")
        await service.set_paused("u1", True)
        await service.set_paused("u1", False)
        await service.recheck_all()
        await service.delete("u1")
        await service.set_interval("u1", 60)

    asyncio.run(scenario())
    assert seen[0][2] == {"recheck": "1"} and seen[1][2] == {"paused": "paused"} and seen[2][2] == {"paused": "unpaused"}
    assert seen[3] == ("GET", "http://127.0.0.1:5000/api/v1/watch", {"recheck_all": "1"})
    assert seen[4][0] == "DELETE" and seen[5][0] == "PUT"


def test_snapshots_and_details_fall_back_to_snapshot_prices():
    def handler(method, url, kw):
        if url.endswith("/watch/p1"):
            return FakeResponse(body={"url": "https://shop.ua/x", "title": "Чайник", "processor": "restock_diff"})
        if url.endswith("/watch/p1/history"):
            return FakeResponse(body={"1700": "/a/1700.txt", "1800": "/a/1800.txt"})
        if url.endswith("/history/1700"):
            return FakeResponse(raw=b"In Stock: True - Price: 1400.0")
        if url.endswith("/history/1800"):
            return FakeResponse(raw=b"In Stock: True - Price: 1200.0")
        raise AssertionError(url)

    service, _ = _service(handler)
    site = asyncio.run(service.details("p1"))
    assert site.is_price and site.price == 1200.0 and site.last_price == 1400.0 and site.in_stock is True
    assert asyncio.run(service.last_two("p1")) == ("In Stock: True - Price: 1400.0", "In Stock: True - Price: 1200.0")


def test_last_two_needs_two_snapshots():
    service, _ = _service(
        lambda m, u, kw: FakeResponse(body={"1700": "x"}) if u.endswith("/history") else FakeResponse(raw="текст".encode())
    )
    assert asyncio.run(service.last_two("u")) is None
    assert asyncio.run(service.last_two("u", allow_single=True)) == ("", "текст")


def test_sites_sorted_paused_last_and_survive_detail_errors():
    data = {
        "a": {"url": "https://a.ua/1", "title": "Старое", "last_changed": 10},
        "b": {"url": "https://b.ua/1", "title": "Свежее", "last_changed": 50},
        "c": {"url": "https://c.ua/1", "title": "Пауза", "last_changed": 99, "paused": True},
    }

    def handler(method, url, kw):
        if url.endswith("/watch"):
            return FakeResponse(body=data)
        if url.endswith("/watch/a"):
            return FakeResponse(status=500)  # карточка не открылась — остаётся короткая запись
        key = url.rsplit("/", 1)[-1]
        return FakeResponse(body=data[key])

    service, _ = _service(handler)
    sites, total = asyncio.run(service.sites(10))
    assert [s.uuid for s in sites] == ["b", "a", "c"] and total == 3


# ------------------------------------------------------------------------------- тексты
def test_money_ago_every():
    assert money(1200.0, "₴") == "1 200 ₴" and money(1299.5, "₴") == "1 299.50 ₴"
    assert ago(0, NOW) == "ещё нет" and ago(int(NOW) - 30, NOW) == "только что"
    assert (
        ago(int(NOW) - 600, NOW) == "10 мин назад"
        and ago(int(NOW) - 7200, NOW) == "2 ч назад"
        and ago(int(NOW) - 3 * 86400, NOW) == "3 дн назад"
    )
    assert every(15) == "15 мин" and every(180) == "3 ч" and every(1440) == "сутки" and every(0) == "как в changedetection"


def test_price_change_texts():
    site = Site("u", "https://www.olx.ua/d/x", "Велосипед <b>")
    drop = render_site_change(site, Change("price", 1400.0, 1200.0, True, True), "₴")
    assert "📉" in drop and "1 400 ₴ → <b>1 200 ₴</b>" in drop and "-14.3%" in drop and "olx.ua" in drop
    assert "&lt;b&gt;" in drop  # название экранировано
    rise = render_site_change(site, Change("price", 1000.0, 1100.0, True, True), "₴")
    assert "📈" in rise and "+10.0%" in rise
    stock = render_site_change(site, Change("price", 1000.0, 1000.0, False, True), "₴")
    assert "Появился в наличии" in stock
    gone = render_site_change(site, Change("price", 1000.0, None, True, True), "₴")
    assert "пропала" in gone


def test_page_change_text_and_error_and_card():
    site = Site("u", "https://olx.ua/list", "Поиск")
    text = render_site_change(site, Change("page", added=("Новое <1>",), removed=("Старое",)), "₴")
    assert "Страница изменилась" in text and "• Новое &lt;1&gt;" in text and "• Старое" in text
    blocked = Site("u", "https://olx.ua/list", "Поиск", error="Error - 403 Forbidden")
    assert "Браузер" in render_site_error(blocked)
    card = render_site_card(
        Site(
            "u",
            "https://olx.ua/x",
            "Вело",
            mode="price",
            price=1200.0,
            last_price=1400.0,
            in_stock=True,
            last_checked=int(NOW) - 60,
            interval_min=60,
        ),
        "₴",
        NOW,
    )
    assert "1 200 ₴" in card and "было 1 400 ₴" in card and "каждые 1 ч" in card and "в наличии" in card
    empty = render_site_card(Site("u", "https://a.ua/x", "Т", mode="price", last_checked=int(NOW) - 5), "₴", NOW)
    assert "найти не удалось" in empty
    assert "Пока ничего" in render_sites([], 0, "₴", NOW)
    listing = render_sites([Site("u", "https://a.ua/x", "Т", mode="price", price=10.0, last_price=12.0)], 4, "₴", NOW)
    assert "📉" in listing and "Показаны первые 1" in listing


# ----------------------------------------------------------------------- фоновый опрос
class FakeBot:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail = False

    async def send_message(self, chat_id, text, reply_markup=None):
        if self.fail:
            raise TelegramAPIError(method=None, message="offline")  # type: ignore[arg-type]
        self.sent.append(text)


class FakeSites:
    def __init__(self) -> None:
        self.sites: list[Site] = []
        self.snapshots: dict[str, tuple[str, str]] = {}
        self.series: dict[str, list[tuple[int, float]]] = {}
        self.error: AppError | None = None

    async def brief(self):
        if self.error is not None:
            raise self.error
        return list(self.sites)

    async def last_two(self, uuid, allow_single=False):
        return self.snapshots.get(uuid)

    async def price_history(self, uuid, limit=150):
        return self.series.get(uuid, [])


def _job(tmp, bot, service):
    store = StateStore(Path(tmp) / "s.json")
    store.load()
    return SitesWatchJob(bot=bot, admin_id=1, service=service, store=store, currency="₴"), store  # type: ignore[arg-type]


def _site(uuid="u1", *, changed=0, error="", paused=False, url="https://olx.ua/d/x") -> Site:
    return Site(uuid, url, "Велосипед", last_changed=changed, error=error, paused=paused)


def test_watch_first_run_is_silent_then_reports_price_drop_once():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot, service = FakeBot(), FakeSites()
            job, store = _job(tmp, bot, service)
            service.sites = [_site(changed=100)]
            assert await job.tick() == 0 and bot.sent == []  # первый запуск: запоминаем, не шумим
            service.sites = [_site(changed=100), _site("u2", changed=0)]
            await job.tick()
            assert bot.sent == []  # новая слежка без изменений
            service.sites = [_site(changed=500)]
            service.snapshots["u1"] = ("In Stock: True - Price: 1400.0", "In Stock: True - Price: 1200.0")
            assert await job.tick() == 1
            assert len(bot.sent) == 1 and "Цена упала" in bot.sent[0] and "1 400 ₴ → <b>1 200 ₴</b>" in bot.sent[0]
            assert await job.tick() == 0 and len(bot.sent) == 1  # то же изменение второй раз не присылаем
            assert store.get(STATE_KEY)["seen"] == {"u1": 500}  # удалённая слежка из памяти ушла

    asyncio.run(scenario())


def test_watch_remembers_state_across_restart_and_retries_undelivered():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot, service = FakeBot(), FakeSites()
            job, _ = _job(tmp, bot, service)
            service.sites = [_site(changed=100)]
            await job.tick()
            service.sites = [_site(changed=200)]
            service.snapshots["u1"] = ("a\nb", "a\nc")
            bot.fail = True
            assert await job.tick() == 0 and bot.sent == []  # не доставили — не считаем сообщённым
            bot.fail = False
            job2, _ = _job(tmp, bot, service)  # «перезапуск»: состояние читается с диска
            assert await job2.tick() == 1 and "Страница изменилась" in bot.sent[0] and "• c" in bot.sent[0]
            job3, _ = _job(tmp, bot, service)
            assert await job3.tick() == 0 and len(bot.sent) == 1

    asyncio.run(scenario())


def test_watch_single_snapshot_and_cleared_history_do_not_notify():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot, service = FakeBot(), FakeSites()
            job, store = _job(tmp, bot, service)
            service.sites = [_site(changed=0)]
            await job.tick()
            service.sites = [_site(changed=300)]
            service.snapshots = {}  # last_two → None: снимок один
            await job.tick()
            assert bot.sent == [] and store.get(STATE_KEY)["seen"]["u1"] == 300
            service.sites = [_site(changed=0)]  # историю очистили
            await job.tick()
            assert store.get(STATE_KEY)["seen"]["u1"] == 0 and bot.sent == []

    asyncio.run(scenario())


def test_watch_site_error_is_reported_once_after_two_ticks():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot, service = FakeBot(), FakeSites()
            job, _ = _job(tmp, bot, service)
            service.sites = [_site()]
            await job.tick()
            service.sites = [_site(error="Error - 403 Forbidden")]
            await job.tick()
            assert bot.sent == []  # одиночный сбой — не повод
            await job.tick()
            assert len(bot.sent) == 1 and "Не могу открыть" in bot.sent[0] and "Браузер" in bot.sent[0]
            await job.tick()
            assert len(bot.sent) == 1  # о той же ошибке второй раз не пишем
            service.sites = [_site()]
            await job.tick()
            service.sites = [_site(error="Error - 403 Forbidden")]
            await job.tick()
            await job.tick()
            assert len(bot.sent) == 2  # прошла и вернулась — сообщаем снова
            service.sites = [_site(error="Error - 403", paused=True)]
            await job.tick()
            await job.tick()
            assert len(bot.sent) == 2  # на паузе — молчим

    asyncio.run(scenario())


def test_watch_reports_unreachable_service_after_series_and_recovery():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot, service = FakeBot(), FakeSites()
            job, _ = _job(tmp, bot, service)
            service.error = AppError("сервис недоступен")
            for _ in range(ERROR_AFTER - 1):
                await job.tick()
            assert bot.sent == []
            await job.tick()
            assert len(bot.sent) == 1 and "не отвечает" in bot.sent[0]
            await job.tick()
            assert len(bot.sent) == 1
            service.error = None
            await job.tick()
            assert len(bot.sent) == 2 and "снова отвечает" in bot.sent[1]

    asyncio.run(scenario())


# ----------------------------------------------------------------------- 3.5: цель, рекорд, несколько ссылок
def test_extract_urls_many_dedup_and_limit():
    text = "https://a.ua/x   b.ua/y\nhttps://a.ua/x  мусор  https://c.ua/z"
    assert extract_urls(text) == ["https://a.ua/x", "https://b.ua/y", "https://c.ua/z"]
    assert len(extract_urls(" ".join(f"https://s{i}.ua/p" for i in range(30)))) == 10
    assert extract_urls("ничего тут нет") == []


def test_check_target_fires_once_and_rearms_after_rise():
    entry = {"price": 1100.0, "armed": True}
    hit, entry = check_target(entry, 1200.0)
    assert hit is False and entry["armed"] is True
    hit, entry = check_target(entry, 1090.0)
    assert hit is True and entry["armed"] is False
    hit, entry = check_target(entry, 1050.0)
    assert hit is False  # ещё ниже — второй раз не сообщаем
    hit, entry = check_target(entry, 1300.0)
    assert hit is False and entry["armed"] is True
    assert check_target(entry, 1000.0)[0] is True
    assert check_target(None, 1.0) == (False, None)
    assert check_target(entry, None) == (False, entry)


def test_set_target_and_target_of():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            store.load()
            assert target_of(store, "u1") is None
            assert await set_target(store, "u1", 1100.0, current=1500.0) is False
            assert target_of(store, "u1") == 1100.0 and store.get(KEY_TARGETS)["u1"]["armed"] is True
            assert await set_target(store, "u2", 1100.0, current=900.0) is True  # уже ниже — ждём роста
            assert store.get(KEY_TARGETS)["u2"]["armed"] is False
            await set_target(store, "u1", None)
            assert target_of(store, "u1") is None

    asyncio.run(scenario())


def test_watch_reports_target_hit_and_record_low():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot, service = FakeBot(), FakeSites()
            job, store = _job(tmp, bot, service)
            service.sites = [_site(changed=100)]
            await job.tick()
            await set_target(store, "u1", 1100.0, current=1500.0)
            service.sites = [_site(changed=500)]
            service.snapshots["u1"] = ("In Stock: True - Price: 1400.0", "In Stock: True - Price: 1050.0")
            service.series["u1"] = [(1, 1500.0), (2, 1400.0), (3, 1050.0)]
            await job.tick()
            assert len(bot.sent) == 1
            assert "цели" in bot.sent[0] and "Самая низкая" in bot.sent[0]
            assert store.get(KEY_TARGETS)["u1"]["armed"] is False

    asyncio.run(scenario())


def test_card_shows_stats_target_and_browser():
    from homebot.services.sites import PriceStats

    site = Site("u", "https://olx.ua/x", "Вело", mode="price", price=1200.0, browser=True)
    stats = PriceStats(points=3, lowest=1000.0, lowest_at=NOW - 86400, highest=1500.0, highest_at=NOW - 3 * 86400, first=1500.0)
    card = render_site_card(site, "₴", NOW, stats, 1100.0)
    assert "1 000 ₴" in card and "1 500 ₴" in card and "Цель: до 1 100 ₴" in card and "браузер" in card
    assert "Цель" not in render_site_card(site, "₴", NOW)
