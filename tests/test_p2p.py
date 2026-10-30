"""💱 P2P-курс Binance: разбор объявлений, цена, история, правила уведомлений, фоновая проверка."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError

from homebot.jobs.p2p_watch import ERROR_AFTER, KEY_STATE, P2PWatchJob
from homebot.services.apps_http import AppError
from homebot.services.charts import NoDataError, render_p2p_chart
from homebot.services.p2p import (
    HISTORY_KEY,
    POINT_GAP_SEC,
    P2PAd,
    P2PQuote,
    P2PService,
    add_point,
    change_percent,
    chart_points,
    crossed_target,
    make_quote,
    parse_ads,
    parse_price,
    stats,
)
from homebot.services.prefs import P2P_THRESHOLDS, P2PPrefs, Prefs, next_choice
from homebot.services.state import StateStore
from homebot.ui.p2p_texts import money, render_p2p, render_p2p_change, render_p2p_summary, render_p2p_target
from tests.fakes import FakeResponse, FakeSession

KYIV = ZoneInfo("Europe/Kyiv")


def _adv(price, *, nick="trader", rate=0.98, orders=120, lo=500, hi=40000, pay="Monobank"):
    return {
        "adv": {
            "price": str(price),
            "minSingleTransAmount": str(lo),
            "maxSingleTransAmount": str(hi),
            "tradeMethods": [{"tradeMethodName": pay}],
        },
        "advertiser": {"nickName": nick, "monthOrderCount": orders, "monthFinishRate": rate},
    }


CLASSIC = {"code": "000000", "data": [_adv(41.6), _adv(41.5, nick="b"), _adv(41.4, nick="c"), _adv(41.3), _adv(41.2), _adv(40.0)]}


# --------------------------------------------------------------------------- разбор
def test_parse_classic_shape():
    ads = parse_ads(CLASSIC)
    assert [a.price for a in ads][:3] == [41.6, 41.5, 41.4]
    first = ads[0]
    assert first.merchant == "trader" and first.finish_rate == 0.98 and first.orders == 120
    assert first.min_amount == 500 and first.max_amount == 40000 and first.methods == ("Monobank",)


def test_parse_flat_and_list_shapes_and_junk():
    flat = {"data": {"ads": [{"price": "41,55", "nickName": "x", "minAmount": 100, "maxAmount": "9000", "completionRate": 97}]}}
    ad = parse_ads(flat)[0]
    assert ad.price == 41.55 and ad.finish_rate == 0.97  # проценты превращены в доли
    assert parse_ads([{"price": 41.0}])[0].price == 41.0
    junk = {"data": [None, "x", {"adv": {"price": "abc"}}, {"adv": {"price": 0}}, {"adv": {"price": True}}, _adv(40.1)]}
    assert [a.price for a in parse_ads(junk)] == [40.1]
    for empty in (None, {}, {"data": None}, {"data": {}}, "text", 5):
        assert parse_ads(empty) == []


def test_make_quote_skips_unreliable_and_takes_median_of_top():
    ads = parse_ads(CLASSIC)
    ads.insert(0, P2PAd(price=50.0, merchant="scam", finish_rate=0.5, orders=3))  # «золотое» объявление ненадёжного продавца
    quote = make_quote(ads, now=1000.0)
    assert quote.best == 41.6 and quote.typical == 41.4 and quote.total == 7 and quote.at == 1000.0
    assert [a.price for a in quote.ads] == [41.6, 41.5, 41.4]


def test_make_quote_buy_side_and_fallbacks():
    ads = [P2PAd(41.0), P2PAd(40.0), P2PAd(42.0)]
    assert make_quote(ads, sell=False).best == 40.0
    only_bad = [P2PAd(41.0, finish_rate=0.1), P2PAd(40.0, orders=1)]
    assert make_quote(only_bad).best == 41.0  # надёжных нет — берём что есть, а не падаем
    try:
        make_quote([])
    except AppError as exc:
        assert "объявлений нет" in str(exc)
    else:
        raise AssertionError("ожидалась ошибка")


def test_ad_without_seller_data_counts_as_reliable():
    assert P2PAd(41.0).reliable
    assert not P2PAd(41.0, finish_rate=0.89).reliable and not P2PAd(41.0, orders=29).reliable


# --------------------------------------------------------------------------- сервис
def test_service_posts_sell_search_with_filters():
    session = FakeSession(lambda m, u, k: FakeResponse(body=CLASSIC))
    service = P2PService(session, fiat="uah", asset="usdt", pay_types=["Monobank"], amount=5000)  # type: ignore[arg-type]
    quote = asyncio.run(service.quote())
    method, url, kwargs = session.calls[0]
    body = kwargs["json"]
    assert method == "POST" and url.endswith("/adv/search") and len(session.calls) == 1
    assert body["fiat"] == "UAH" and body["asset"] == "USDT" and body["tradeType"] == "SELL"
    assert body["payTypes"] == ["Monobank"] and body["transAmount"] == "5000"
    assert quote.best == 41.6


def test_service_omits_amount_when_zero():
    session = FakeSession(lambda m, u, k: FakeResponse(body=CLASSIC))
    asyncio.run(P2PService(session).quote())  # type: ignore[arg-type]
    assert "transAmount" not in session.calls[0][2]["json"]


def test_service_falls_back_to_public_list_on_error_and_on_empty():
    def handler(method, url, kwargs):
        if method == "POST":
            return FakeResponse(status=403)
        return FakeResponse(body={"data": [{"price": "41.1"}, {"price": "41.0"}]})

    session = FakeSession(handler)
    quote = asyncio.run(P2PService(session).quote())  # type: ignore[arg-type]
    assert [c[0] for c in session.calls] == ["POST", "GET"] and quote.best == 41.1
    assert session.calls[1][2]["params"]["tradeType"] == "SELL"

    def empty_then_list(method, url, kwargs):
        return (
            FakeResponse(body={"code": "000000", "data": []}) if method == "POST" else FakeResponse(body={"data": [{"price": 40.5}]})
        )

    assert asyncio.run(P2PService(FakeSession(empty_then_list)).quote()).best == 40.5  # type: ignore[arg-type]


def test_service_reports_original_error_when_both_fail():
    def handler(method, url, kwargs):
        return FakeResponse(status=451) if method == "POST" else FakeResponse(status=500)

    try:
        asyncio.run(P2PService(FakeSession(handler)).quote())  # type: ignore[arg-type]
    except AppError as exc:
        assert "451" in str(exc)
    else:
        raise AssertionError("ожидалась ошибка")


def test_service_api_error_code_is_reported():
    def handler(method, url, kwargs):
        if method == "POST":
            return FakeResponse(body={"code": "000002", "message": "illegal parameter", "data": None})
        return FakeResponse(status=404)

    try:
        asyncio.run(P2PService(FakeSession(handler)).quote())  # type: ignore[arg-type]
    except AppError as exc:
        assert "illegal parameter" in str(exc)
    else:
        raise AssertionError("ожидалась ошибка")


# --------------------------------------------------------------------------- история и правила
def _quote(at, typical, best=None):
    return P2PQuote(at=at, best=best or typical, typical=typical, ads=(P2PAd(typical),), total=5)


def test_history_gap_trim_stats_and_chart_points():
    history = add_point(None, _quote(1000.0, 41.0))
    assert history == [[1000.0, 41.0, 41.0]]
    history = add_point(history, _quote(1000.0 + POINT_GAP_SEC - 1, 41.5))
    assert len(history) == 1  # слишком рано — точка не добавляется
    history = add_point(history, _quote(1000.0 + POINT_GAP_SEC, 41.5))
    assert len(history) == 2
    week_later = 1000.0 + 9 * 86400
    assert add_point(history, _quote(week_later, 40.0)) == [[week_later, 40.0, 40.0]]  # старше 8 суток отрезано
    assert add_point("junk", _quote(1.0, 1.0)) == [[1.0, 1.0, 1.0]]

    series = [[0.0, 40.0, 40.0], [3600.0, 42.0, 41.0], [7200.0, 43.0, 42.0], "junk", [1.0]]
    day = stats(series, now=7200.0)
    assert day is not None and (day.low, day.high, day.points) == (40.0, 42.0, 3)
    assert round(day.change_percent, 2) == 5.0 and round(day.average, 2) == 41.0
    assert stats([], now=0) is None and stats("x", now=0) is None
    assert stats(series, now=10**7) is None
    assert len(chart_points(series, now=7200.0)) == 3 and chart_points(None, now=0) == []


def test_change_percent():
    assert change_percent(40.0, 41.0) == 2.5 and change_percent(None, 41.0) == 0.0 and change_percent(0, 41.0) == 0.0


def test_crossed_target_fires_once_and_rearms_with_hysteresis():
    assert crossed_target(0.0, 50.0, True) == (False, True)  # цели нет
    assert crossed_target(42.0, 41.0, True) == (False, True)  # ещё не дошла
    assert crossed_target(42.0, 42.0, True) == (True, False)  # дошла — сообщаем
    assert crossed_target(42.0, 43.0, False) == (False, False)  # уже сообщали
    assert crossed_target(42.0, 41.98, False) == (False, False)  # дребезг у цели не взводит заново
    assert crossed_target(42.0, 41.9, False) == (False, True)  # опустилась заметно ниже — взводим


def test_parse_price():
    for text, value in (("41.5", 41.5), ("41,5 грн", 41.5), ("₴ 42", 42.0), ("  41.55 UAH ", 41.55), ("1 000", 1000.0)):
        assert parse_price(text) == value, text
    for bad in ("", "abc", "0", "-5", "nan", "inf", "99999999", "41.5.5"):
        assert parse_price(bad) is None, bad


# --------------------------------------------------------------------------- настройки
def test_prefs_defaults_update_and_persist():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            cfg = SimpleNamespace(news_quiet_hours="", feeds=[], news_enabled=True, news_interval_min=60, news_count=5)
            prefs = Prefs(store, cfg)  # type: ignore[arg-type]
            assert prefs.p2p() == P2PPrefs(1.0, 0.0, True)
            await prefs.update_p2p(target=41.5, threshold=0.0)
            again = Prefs(store, cfg).p2p()  # type: ignore[arg-type]
            assert again == P2PPrefs(0.0, 41.5, True)
            await store.set("prefs.p2p", {"threshold": "bad"})
            assert Prefs(store, cfg).p2p() == P2PPrefs()  # type: ignore[arg-type]

    asyncio.run(scenario())


def test_threshold_cycle_covers_all_values_and_wraps():
    seen = []
    value = P2P_THRESHOLDS[0]
    for _ in range(len(P2P_THRESHOLDS)):
        value = next_choice(P2P_THRESHOLDS, value)
        seen.append(value)
    assert set(seen) == set(P2P_THRESHOLDS) and seen[-1] == P2P_THRESHOLDS[0]
    assert next_choice(P2P_THRESHOLDS, 7.7) == P2P_THRESHOLDS[0]  # неизвестное значение — с начала


# --------------------------------------------------------------------------- фоновая проверка
class FakeBot:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail = False
        self.markups: list[object] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        if self.fail:
            raise TelegramAPIError(method=None, message="offline")  # type: ignore[arg-type]
        self.sent.append(text)
        self.markups.append(reply_markup)


class FakeService:
    asset, fiat = "USDT", "UAH"

    def __init__(self) -> None:
        self.typical = 41.0
        self.error: AppError | None = None
        self.now = 1_000_000.0

    async def quote(self):
        if self.error is not None:
            raise self.error
        return _quote(self.now, self.typical, self.typical + 0.2)


def _job(tmp, bot, service, prefs, *, clock=None, **kw):
    store = StateStore(Path(tmp) / "s.json")
    store.load()  # как при старте процесса: подхватываем то, что осталось на диске
    job = P2PWatchJob(
        bot=bot,  # type: ignore[arg-type]
        admin_id=1,
        service=service,  # type: ignore[arg-type]
        store=store,
        prefs=lambda: prefs[0],
        tz=KYIV,
        interval=300,
        summary_hour=21,
        clock=clock or (lambda: service.now),
        **kw,
    )
    return job, store


def test_watch_sharp_change_baseline_and_threshold():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot, service, prefs = FakeBot(), FakeService(), [P2PPrefs(threshold=1.0, summary=False)]
            job, store = _job(tmp, bot, service, prefs)
            await job.tick()
            assert bot.sent == [] and store.get(KEY_STATE)["baseline"] == 41.0  # первый запуск — только запоминаем
            service.typical, service.now = 41.3, service.now + 700
            await job.tick()
            assert bot.sent == []  # +0.7% < порога
            service.typical, service.now = 41.5, service.now + 700
            await job.tick()
            assert len(bot.sent) == 1 and "41.50" in bot.sent[0] and "+1.2%" in bot.sent[0] and "📈" in bot.sent[0]
            assert store.get(KEY_STATE)["baseline"] == 41.5  # точка отсчёта сдвинулась
            service.typical, service.now = 40.0, service.now + 700
            await job.tick()
            assert len(bot.sent) == 2 and "📉" in bot.sent[1] and "-3.6%" in bot.sent[1]
            assert len(store.get(HISTORY_KEY)) == 4

    asyncio.run(scenario())


def test_watch_threshold_off_never_reports_change_but_follows_price():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot, service, prefs = FakeBot(), FakeService(), [P2PPrefs(threshold=0.0, summary=False)]
            job, store = _job(tmp, bot, service, prefs)
            await job.tick()
            service.typical = 45.0
            await job.tick()
            assert bot.sent == [] and store.get(KEY_STATE)["baseline"] == 45.0

    asyncio.run(scenario())


def test_watch_undelivered_change_is_retried():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot, service, prefs = FakeBot(), FakeService(), [P2PPrefs(threshold=1.0, summary=False)]
            job, store = _job(tmp, bot, service, prefs)
            await job.tick()
            service.typical = 42.0
            bot.fail = True
            await job.tick()
            assert bot.sent == [] and store.get(KEY_STATE)["baseline"] == 41.0  # не доставлено — не считаем сообщённым
            bot.fail = False
            await job.tick()
            assert len(bot.sent) == 1 and "42.00" in bot.sent[0]

    asyncio.run(scenario())


def test_watch_target_once_and_rearm():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot, service = FakeBot(), FakeService()
            prefs = [P2PPrefs(threshold=0.0, target=41.5, summary=False)]
            job, store = _job(tmp, bot, service, prefs)
            await job.tick()
            assert bot.sent == []
            service.typical = 41.6
            await job.tick()
            await job.tick()
            assert len(bot.sent) == 1 and "Цель достигнута" in bot.sent[0] and "41.50" in bot.sent[0]
            service.typical = 41.0  # опустилась — взводим
            await job.tick()
            assert store.get(KEY_STATE)["armed"] is True
            service.typical = 41.7
            await job.tick()
            assert len(bot.sent) == 2

    asyncio.run(scenario())


def test_watch_target_not_marked_when_delivery_fails():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot, service = FakeBot(), FakeService()
            prefs = [P2PPrefs(threshold=0.0, target=41.0, summary=False)]
            job, store = _job(tmp, bot, service, prefs)
            bot.fail = True
            await job.tick()
            assert store.get(KEY_STATE)["armed"] is True
            bot.fail = False
            await job.tick()
            assert len(bot.sent) == 1 and store.get(KEY_STATE)["armed"] is False

    asyncio.run(scenario())


def test_watch_daily_summary_once_after_hour():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot, service, prefs = FakeBot(), FakeService(), [P2PPrefs(threshold=0.0, summary=True)]
            clock = [datetime(2026, 10, 21, 20, 30, tzinfo=KYIV).timestamp()]
            service.now = clock[0]
            job, _ = _job(tmp, bot, service, prefs, clock=lambda: clock[0])
            await job.tick()
            assert bot.sent == []  # ещё нет 21:00
            clock[0] = datetime(2026, 10, 21, 21, 5, tzinfo=KYIV).timestamp()
            service.now, service.typical = clock[0], 41.8
            await job.tick()
            assert len(bot.sent) == 1 and "итог дня" in bot.sent[0] and "41.80" in bot.sent[0]
            clock[0] += 600
            service.now = clock[0]
            await job.tick()
            assert len(bot.sent) == 1  # в тот же день повторно — нет
            clock[0] = datetime(2026, 10, 22, 21, 10, tzinfo=KYIV).timestamp()
            service.now = clock[0]
            await job.tick()
            assert len(bot.sent) == 2  # на следующий день — снова
            prefs[0] = P2PPrefs(threshold=0.0, summary=False)
            clock[0] += 86400
            service.now = clock[0]
            await job.tick()
            assert len(bot.sent) == 2  # выключено — молчим

    asyncio.run(scenario())


def test_watch_reports_long_outage_once_and_recovery():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot, service, prefs = FakeBot(), FakeService(), [P2PPrefs(threshold=0.0, summary=False)]
            job, _ = _job(tmp, bot, service, prefs, markup=lambda: "KB")
            service.error = AppError("HTTP 451")
            for _ in range(ERROR_AFTER + 3):
                assert await job.tick() is None
            assert len(bot.sent) == 1 and "HTTP 451" in bot.sent[0] and bot.markups == ["KB"]
            service.error = None
            assert await job.tick() is not None
            assert len(bot.sent) == 2 and "снова обновляется" in bot.sent[1]
            await job.tick()
            assert len(bot.sent) == 2

    asyncio.run(scenario())


def test_watch_survives_restart_without_repeating():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot, service = FakeBot(), FakeService()
            prefs = [P2PPrefs(threshold=1.0, target=41.5, summary=False)]
            job, _ = _job(tmp, bot, service, prefs)
            await job.tick()
            service.typical = 41.6
            await job.tick()
            assert len(bot.sent) == 2  # изменение +1.5% и цель
            again, _ = _job(tmp, bot, service, prefs)  # новый процесс, то же хранилище
            await again.tick()
            assert len(bot.sent) == 2

    asyncio.run(scenario())


# --------------------------------------------------------------------------- тексты и график
def test_texts():
    quote = make_quote(parse_ads(CLASSIC), now=datetime(2026, 10, 21, 12, 0, tzinfo=KYIV).timestamp())
    day = stats([[quote.at - 3600, 40.0, 40.0], [quote.at, 41.5, 41.4]], now=quote.at)
    text = render_p2p(
        quote, error="", day=day, prefs=P2PPrefs(2.0, 42.0, True), asset="USDT", fiat="UAH", amount=5000, methods=["Monobank"], tz=KYIV
    )
    assert "Продать USDT за UAH" in text and "41.40 ₴" in text and "41.60 ₴" in text and "12:00" in text
    assert "сумма 5 000" in text and "Monobank" in text and "±2%" in text and "42.00 ₴" in text and "trader" in text
    broken = render_p2p(
        None, error="HTTP 451 <b>", day=None, prefs=P2PPrefs(0.0, 0.0, False), asset="USDT", fiat="UAH", amount=0, methods=[], tz=KYIV
    )
    assert "HTTP 451 &lt;b&gt;" in broken and "любые объявления" in broken and "выключено" in broken and "не задана" in broken
    assert money(1234.5, "UAH") == "1 234.50 ₴" and money(10, "XXX") == "10.00 XXX"
    assert "-2.4%" in render_p2p_change(_quote(0, 40.0), 41.0, asset="USDT", fiat="UAH")
    assert "Цель достигнута" in render_p2p_target(_quote(0, 42.5), 42.0, asset="USDT", fiat="UAH")
    summary = render_p2p_summary(quote, day, asset="USDT", fiat="UAH")
    assert (
        "итог дня" in summary and "Минимум" in summary and "Минимум" not in render_p2p_summary(quote, None, asset="USDT", fiat="UAH")
    )


def test_chart_renders_png_and_needs_two_points():
    points = [(datetime(2026, 10, 15 + i, 12, tzinfo=KYIV), 41.5 + i * 0.1, 41.3 + i * 0.1) for i in range(6)]
    png = render_p2p_chart(points, tz=KYIV, title="USDT → UAH", fiat="UAH")
    assert png.startswith(b"\x89PNG")
    try:
        render_p2p_chart(points[:1], tz=KYIV, title="x")
    except NoDataError:
        pass
    else:
        raise AssertionError("ожидалась ошибка")
