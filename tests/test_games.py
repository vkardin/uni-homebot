import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError

from homebot.jobs.games_watch import FreeGamesJob
from homebot.services.apps_http import AppError
from homebot.services.free_games import FreeGame, GamesService, parse_epic, parse_gamerpower
from homebot.services.state import StateStore
from homebot.ui.extra_texts import render_free_game
from tests.fakes import FakeResponse, FakeSession

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def epic_element(**over):
    base = {
        "title": "System Shock 2: 25th Anniversary Remaster",
        "id": "7ae00c3e89174012b175cc73b4737723",
        "status": "ACTIVE",
        "productSlug": None,
        "urlSlug": "system-shock-2-25th-anniversary-remaster",
        "keyImages": [
            {"type": "OfferImageTall", "url": "https://cdn1.epicgames.com/tall.jpg"},
            {"type": "OfferImageWide", "url": "https://cdn1.epicgames.com/wide.jpg"},
        ],
        "offerMappings": [{"pageSlug": "system-shock-2-25th-anniversary-remaster-cb94d9", "pageType": "productHome"}],
        "catalogNs": {"mappings": [{"pageSlug": "other", "pageType": "productHome"}]},
        "price": {"totalPrice": {"discountPrice": 0, "originalPrice": 54900, "fmtPrice": {"originalPrice": "549,00 ₴"}}},
        "promotions": {
            "promotionalOffers": [
                {
                    "promotionalOffers": [
                        {
                            "startDate": "2026-10-01T15:00:00.000Z",
                            "endDate": "2026-10-08T15:00:00.000Z",
                            "discountSetting": {"discountType": "PERCENTAGE", "discountPercentage": 0},
                        }
                    ]
                }
            ],
            "upcomingPromotionalOffers": [],
        },
    }
    base.update(over)
    return base


def epic_payload(*elements):
    return {"data": {"Catalog": {"searchStore": {"elements": list(elements)}}}}


def test_parse_epic_picks_only_games_free_right_now():
    sale = epic_element(
        title="Ghostrunner 2", id="g2",
        price={"totalPrice": {"discountPrice": 79900, "originalPrice": 99900, "fmtPrice": {"originalPrice": "999,00 ₴"}}},
        promotions={"promotionalOffers": [{"promotionalOffers": [
            {"startDate": "2026-10-01T15:00:00.000Z", "endDate": "2026-10-08T15:00:00.000Z",
             "discountSetting": {"discountPercentage": 80}}]}], "upcomingPromotionalOffers": []},
    )  # fmt: skip
    upcoming = epic_element(
        title="Out of Sight", id="oos",
        promotions={"promotionalOffers": [], "upcomingPromotionalOffers": [{"promotionalOffers": [
            {"startDate": "2026-10-08T15:00:00.000Z", "endDate": "2026-10-15T15:00:00.000Z",
             "discountSetting": {"discountPercentage": 0}}]}]},
    )  # fmt: skip
    always_free = epic_element(title="Free forever", id="ff", price={"totalPrice": {"discountPrice": 0, "originalPrice": 0}})
    expired = epic_element(title="Old", id="old", promotions={"promotionalOffers": [{"promotionalOffers": [
        {"startDate": "2026-09-01T15:00:00.000Z", "endDate": "2026-09-08T15:00:00.000Z",
         "discountSetting": {"discountPercentage": 0}}]}]})  # fmt: skip
    games = parse_epic(epic_payload(epic_element(), sale, upcoming, always_free, expired, "junk", {"title": "no id"}), NOW)
    assert [g.title for g in games] == ["System Shock 2: 25th Anniversary Remaster"]
    game = games[0]
    assert game.store == "Epic Games Store" and game.worth == "549,00 ₴"
    assert game.url == "https://store.epicgames.com/ru/p/system-shock-2-25th-anniversary-remaster-cb94d9"
    assert game.image == "https://cdn1.epicgames.com/wide.jpg"
    assert game.end == datetime(2026, 10, 8, 15, 0, tzinfo=UTC) and game.key.startswith("epic:7ae0")
    assert game.is_active(NOW) and not game.is_active(game.end)


def test_parse_epic_rejects_unexpected_shape_and_falls_back_to_free_games_page():
    for bad in ({}, None, {"data": {}}, []):
        try:
            parse_epic(bad, NOW)
        except AppError:
            pass
        else:
            raise AssertionError("ожидали AppError")
    no_slug = epic_element(offerMappings=None, catalogNs=None, urlSlug=None, productSlug=None)
    assert parse_epic(epic_payload(no_slug), NOW)[0].url == "https://store.epicgames.com/ru/free-games"
    by_product = epic_element(offerMappings=None, catalogNs=None, productSlug="some-game/home")
    assert parse_epic(epic_payload(by_product), NOW)[0].url.endswith("/p/some-game")


GAMERPOWER = [
    {"id": 1, "title": "Fallout 3 (Steam) Giveaway", "worth": "$9.99", "image": "https://x/i.jpg", "type": "Game",
     "platforms": "PC, Steam", "end_date": "2026-10-06 23:59:00", "status": "Active",
     "open_giveaway_url": "https://www.gamerpower.com/open/fallout-3", "published_date": "2026-10-01 10:00:00"},
    {"id": 2, "title": "Some DLC", "type": "DLC", "platforms": "PC, Steam", "status": "Active", "open_giveaway_url": "https://x"},
    {"id": 3, "title": "Epic thing", "type": "Game", "platforms": "PC, Epic Games Store", "status": "Active", "open_giveaway_url": "https://x"},
    {"id": 4, "title": "GOG Game", "worth": "N/A", "type": "Game", "platforms": "PC, GOG", "end_date": "N/A", "status": "Active",
     "gamerpower_url": "https://www.gamerpower.com/gog-game"},
    {"id": 5, "title": "Expired", "type": "Game", "platforms": "PC, Steam", "end_date": "2026-09-01 00:00:00", "status": "Active",
     "open_giveaway_url": "https://x"},
    {"id": 6, "title": "Closed", "type": "Game", "platforms": "PC, Steam", "status": "Expired", "open_giveaway_url": "https://x"},
    "junk",
]  # fmt: skip


def test_parse_gamerpower_filters_and_cleans_titles():
    games = parse_gamerpower(GAMERPOWER, NOW)
    assert [(g.title, g.store) for g in games] == [("Fallout 3", "Steam"), ("GOG Game", "GOG")]
    assert games[0].worth == "$9.99" and games[0].end == datetime(2026, 10, 6, 23, 59, tzinfo=UTC)
    assert games[1].worth == "" and games[1].end is None and games[1].url.endswith("gog-game")
    assert parse_gamerpower({"status": 0, "status_message": "No active giveaways"}, NOW) == []


def test_games_service_merges_sources_and_survives_one_failing():
    async def scenario():
        def handler(method, url, kwargs):
            if "epicgames" in url:
                return FakeResponse(body=epic_payload(epic_element()))
            return FakeResponse(body=GAMERPOWER)

        service = GamesService(FakeSession(handler), country="ua")  # type: ignore[arg-type]
        games = await service.fetch(NOW)
        assert [g.store for g in games] == ["Epic Games Store", "Steam", "GOG"]

        def epic_down(method, url, kwargs):
            return FakeResponse(status=503) if "epicgames" in url else FakeResponse(body=GAMERPOWER)

        assert len(await GamesService(FakeSession(epic_down)).fetch(NOW)) == 2  # type: ignore[arg-type]

        def all_down(method, url, kwargs):
            return FakeResponse(status=500)

        try:
            await GamesService(FakeSession(all_down)).fetch(NOW)  # type: ignore[arg-type]
        except AppError:
            pass
        else:
            raise AssertionError("когда упали все источники, ошибку нужно показать")

        only_epic = FakeSession(handler)
        assert len(await GamesService(only_epic, extra_sources=False).fetch(NOW)) == 1  # type: ignore[arg-type]
        assert len(only_epic.calls) == 1
        params = only_epic.calls[0][2]["params"]
        assert params["country"] == "UA" and params["allowCountries"] == "UA"

    asyncio.run(scenario())


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []
        self.fail = False

    async def send_message(self, chat_id, text, link_preview_options=None):
        if self.fail:
            raise TelegramAPIError(method=None, message="offline")  # type: ignore[arg-type]
        self.sent.append((text, link_preview_options))
        return SimpleNamespace(message_id=len(self.sent))


def game(key="epic:1", end_hours=48, **kw):
    return FreeGame(
        key=key, store="Epic Games Store", title=kw.pop("title", "Игра <1>"), url="https://store.example/p?a=1&b=2",
        image="https://img/x.jpg", worth="549 ₴", start=NOW - timedelta(days=1), end=NOW + timedelta(hours=end_hours), **kw,
    )  # fmt: skip


def test_free_games_job_announces_once_then_reminds_once_in_last_day():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            bot = FakeBot()
            current = [game()]

            async def fetch():
                return list(current)

            job = FreeGamesJob(bot=bot, admin_id=1, store=store, fetch=fetch, tz=KYIV, preview=lambda url: f"PREVIEW:{url}")  # type: ignore[arg-type]
            assert await job.tick(NOW) == 1
            assert bot.sent[0][1] == "PREVIEW:https://img/x.jpg"
            text = bot.sent[0][0]
            assert "Бесплатно в Epic Games Store" in text and "Игра &lt;1&gt;" in text and "a=1&amp;b=2" in text
            assert await job.tick(NOW + timedelta(hours=2)) == 0  # второй раз то же самое — молчим
            assert await job.tick(NOW + timedelta(hours=30)) == 1  # осталось < суток → напоминание
            assert "Последние сутки" in bot.sent[1][0]
            assert await job.tick(NOW + timedelta(hours=31)) == 0  # напоминание тоже один раз

            current[:] = [game(key="epic:2", end_hours=50, title="Срочная")]  # объявлена уже в последние сутки
            assert await job.tick(NOW + timedelta(hours=40)) == 1
            assert await job.tick(NOW + timedelta(hours=41)) == 0  # дублирующего напоминания нет

            restarted = FreeGamesJob(bot=bot, admin_id=1, store=store, fetch=fetch, tz=KYIV, preview=lambda url: None)  # type: ignore[arg-type]
            assert await restarted.tick(NOW + timedelta(hours=42)) == 0  # после перезапуска бота без повторов

    asyncio.run(scenario())


def test_free_games_job_retries_failed_delivery_and_ignores_finished_offers():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            bot = FakeBot()

            async def fetch():
                return [game(), game(key="epic:old", end_hours=-1, title="Прошла")]

            job = FreeGamesJob(bot=bot, admin_id=1, store=store, fetch=fetch, tz=KYIV, preview=lambda url: None)  # type: ignore[arg-type]
            bot.fail = True
            assert await job.tick(NOW) == 0
            bot.fail = False
            assert await job.tick(NOW) == 1  # сообщение не потеряно
            assert len(bot.sent) == 1 and "Прошла" not in bot.sent[0][0]

    asyncio.run(scenario())


def test_render_free_game_without_end_date_and_with_hours_left():
    open_end = FreeGame(key="k", store="GOG", title="T", url="https://x/y", worth="")
    text = render_free_game(open_end, KYIV, NOW)
    assert "⏳" not in text and "Бесплатно в GOG" in text and "(обычно" not in text
    soon = render_free_game(game(end_hours=5), KYIV, NOW)
    assert "осталось ~5 ч" in soon and "окт" in soon
    assert "до 5 окт" in render_free_game(game(end_hours=48), KYIV, NOW).replace("\xa0", " ")
