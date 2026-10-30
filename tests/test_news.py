import asyncio
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from homebot.config import Settings
from homebot.jobs.hourly_news import next_slot
from homebot.services.db import Database
from homebot.services.feeds import NewsItem, clean_summary, parse_date, parse_feed
from homebot.services.news import Candidate, NewsService, parse_retell, select
from homebot.services.prefs import COUNT_CHOICES, NewsPrefs, Prefs, next_choice
from homebot.services.state import StateStore
from homebot.ui.texts import render_news_screen
from tests.fakes import FakeResponse, FakeSession

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


def rss(*items: tuple[str, str | None, str]) -> bytes:
    """items: (title, pubDate|None, description)."""
    body = "".join(
        f"<item><title>{t}</title><link>https://ex.test/{abs(hash(t))}</link><guid>{t}</guid>"
        + (f"<pubDate>{d}</pubDate>" if d else "")
        + f"<description><![CDATA[{desc}]]></description></item>"
        for t, d, desc in items
    )
    return f'<rss version="2.0"><channel><title>Feed</title>{body}</channel></rss>'.encode()


def rfc(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


# --------------------------------------------------------------------- ленты
def test_dates_summary_and_uid_are_parsed():
    raw = rss(
        (
            "Fresh",
            rfc(NOW - timedelta(minutes=20)),
            "<p>Real <b>summary</b> &amp; text.</p> The post Fresh appeared first on Site.",
        ),
        ("NoDate", None, "x"),
    )
    _, items = parse_feed(raw, 10)
    assert items[0].published == NOW - timedelta(minutes=20)
    assert items[0].summary == "Real summary & text."  # HTML и хвост WordPress убраны
    assert items[0].uid == "Fresh" and items[1].published is None
    assert items[0].key != items[1].key

    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>A</title><entry><title>E</title><id>urn:1</id>
      <updated>2026-10-13T09:30:00+03:00</updated><summary>Sum</summary><link href="https://a.test/e"/></entry></feed>"""
    _, entries = parse_feed(atom, 5)
    assert entries[0].published == datetime(2026, 10, 13, 6, 30, tzinfo=UTC) and entries[0].summary == "Sum"


def test_parse_date_variants():
    assert parse_date("Mon, 12 Oct 2026 08:30:00 +0300") == datetime(2026, 10, 12, 5, 30, tzinfo=UTC)
    assert parse_date("2026-10-12T08:30:00Z") == datetime(2026, 10, 12, 8, 30, tzinfo=UTC)
    assert parse_date("2026-10-12 08:30:00").tzinfo is not None
    assert parse_date("") is None and parse_date("вчера") is None
    assert len(clean_summary("слово " * 1000)) <= 1201


# --------------------------------------------------------------------- отбор
def cand(title: str, feed: str, minutes_ago: int | None) -> Candidate:
    published = NOW - timedelta(minutes=minutes_ago) if minutes_ago is not None else None
    return Candidate(NewsItem(title, f"https://{feed}/{title}", published, "", title), feed, feed)


def test_select_round_robin_dedupe_and_order():
    candidates = [
        cand("a1", "A", 1), cand("a2", "A", 2), cand("a3", "A", 3), cand("a4", "A", 4),
        cand("b1", "B", 30), cand("B1!", "C", 31),  # дубль заголовка из другой ленты
        cand("c1", "C", 50),
    ]  # fmt: skip
    picked = select(candidates, 4, now=NOW)
    titles = [c.item.title for c in picked]
    assert {"b1", "c1"} <= set(titles)  # каждая лента представлена, хотя A свежее
    assert "B1!" not in titles  # дубль отброшен
    assert titles == sorted(titles, key=lambda t: next(c.item.published for c in candidates if c.item.title == t), reverse=True)
    assert len(select(candidates, 10, now=NOW)) == 6


def test_parse_retell_robust():
    good = json.dumps({"items": [{"n": 2, "title": "Т2", "summary": "С2"}, {"n": 1, "title": "Т1", "summary": "С1"}]})
    assert parse_retell(good, 2) == {0: ("Т1", "С1"), 1: ("Т2", "С2")}
    wrapped = "Вот ответ: " + good + " спасибо"
    assert parse_retell(wrapped, 2)[0] == ("Т1", "С1")
    bad = json.dumps({"items": [{"n": 9, "title": "x", "summary": "y"}, {"n": "x"}, {"n": 1, "title": "", "summary": "y"}]})
    assert parse_retell(bad, 2) == {}
    assert parse_retell("not json at all", 2) == {}


# ---------------------------------------------------------------- сервис целиком
class FakeAsker:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict | None]] = []
        self.fail = fail

    async def ask(self, system, text, *, model=None, schema=None):
        self.calls.append((text, schema))
        if self.fail:
            from homebot.services.ollama import OllamaAPIError

            raise OllamaAPIError("model not found")
        count = text.count("Заголовок:")
        return json.dumps({"items": [{"n": i, "title": f"Заголовок {i}", "summary": f"Пересказ {i}."} for i in range(1, count + 1)]})


def _prefs(**kw) -> NewsPrefs:
    return NewsPrefs(**{"feeds": ("https://a.test/rss", "https://b.test/rss"), "count": 5, "fetch_articles": False, **kw})


def test_service_first_run_baseline_then_only_new():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "db.sqlite")
            db.open()
            feeds_data = {
                "https://a.test/rss": rss(
                    ("A fresh", rfc(NOW - timedelta(minutes=30)), "summary a fresh " * 20),
                    ("A old", rfc(NOW - timedelta(hours=5)), "old"),
                ),
                "https://b.test/rss": rss(("B undated", None, "undated item")),
            }
            session = FakeSession(lambda m, url, kw: FakeResponse(raw=feeds_data[url]))
            asker = FakeAsker()
            service = NewsService(session, db, asker)  # type: ignore[arg-type]

            batch = await service.run(_prefs(), now=NOW, window=timedelta(hours=1))
            assert [s.title for s in batch.stories] == ["Заголовок 1"]  # только свежая с датой
            assert batch.stories[0].retold and batch.stories[0].text == "Пересказ 1."
            assert any(schema is not None for _, schema in asker.calls)  # пересказ — запросом со схемой JSON
            await service.mark_sent(batch.stories)
            assert [n.title for n in await db.recent_sent_news()] == ["Заголовок 1"]

            # второй проход: те же ленты — ничего нового
            again = await service.run(_prefs(), now=NOW + timedelta(hours=1), window=timedelta(hours=1))
            assert again.stories == []

            # в знакомой ленте появилась новость без даты — теперь она новая
            feeds_data["https://b.test/rss"] = rss(("B undated", None, "x"), ("B new undated", None, "fresh"))
            third = await service.run(_prefs(retell=False), now=NOW + timedelta(hours=2), window=timedelta(hours=1))
            assert [s.title for s in third.stories] == ["B new undated"] and not third.stories[0].retold
            db.close()

    asyncio.run(scenario())


def test_service_survives_bad_feed_and_model_failure():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "db.sqlite")
            db.open()

            def handler(method, url, kw):
                if "a.test" in url:
                    return FakeResponse(raw=rss(("Fresh", rfc(NOW - timedelta(minutes=5)), "Описание новости")))
                return FakeResponse(status=500)

            service = NewsService(FakeSession(handler), db, FakeAsker(fail=True))  # type: ignore[arg-type]
            batch = await service.run(_prefs(), now=NOW, window=timedelta(hours=1))
            assert [s.title for s in batch.stories] == ["Fresh"]  # без модели — оригинал
            assert batch.stories[0].text == "Описание новости" and not batch.stories[0].retold
            assert batch.errors == ["b.test: HTTP 500"]
            db.close()

    asyncio.run(scenario())


def test_check_feed():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "db.sqlite")
            db.open()

            def handler(method, url, kw):
                if "good" in url:
                    return FakeResponse(raw=rss(("x", None, "y")))
                if "html" in url:
                    return FakeResponse(raw=b"<html><body>not a feed</body></html>")
                return FakeResponse(status=404)

            service = NewsService(FakeSession(handler), db, FakeAsker())  # type: ignore[arg-type]
            assert await service.check_feed("https://good.test/rss") == (True, "Feed")
            ok, reason = await service.check_feed("https://html.test/")
            assert not ok and "формат" in reason
            ok, reason = await service.check_feed("https://missing.test/rss")
            assert not ok and "404" in reason
            db.close()

    asyncio.run(scenario())


# --------------------------------------------------------------- расписание
def test_next_slot_alignment():
    at = datetime(2026, 10, 13, 9, 17, tzinfo=KYIV)
    assert next_slot(at, 60, KYIV) == datetime(2026, 10, 13, 10, 0, tzinfo=KYIV)
    assert next_slot(at, 30, KYIV) == datetime(2026, 10, 13, 9, 30, tzinfo=KYIV)
    assert next_slot(at, 120, KYIV) == datetime(2026, 10, 13, 10, 0, tzinfo=KYIV)
    assert next_slot(datetime(2026, 10, 13, 10, 0, tzinfo=KYIV), 60, KYIV) == datetime(2026, 10, 13, 11, 0, tzinfo=KYIV)
    assert next_slot(datetime(2026, 10, 13, 23, 40, tzinfo=KYIV), 60, KYIV) == datetime(2026, 10, 14, 0, 0, tzinfo=KYIV)


def test_next_slot_is_always_in_the_future_across_dst_change():
    start = datetime(2026, 10, 24, 20, 0, tzinfo=UTC)  # ночь, когда в Киеве переводят часы (25.10, 04:00 → 03:00)
    for interval in (30, 60, 120):
        for step in range(0, 12 * 6):
            now = start + timedelta(minutes=10 * step)
            slot = next_slot(now, interval, KYIV)
            assert slot > now and slot - now <= timedelta(minutes=interval + 60), (interval, now, slot)


def test_quiet_hours_and_choices():
    night = NewsPrefs(quiet=True, quiet_from=23, quiet_to=8)
    assert night.is_quiet(23) and night.is_quiet(3) and not night.is_quiet(8) and not night.is_quiet(15)
    day = NewsPrefs(quiet=True, quiet_from=13, quiet_to=15)
    assert day.is_quiet(14) and not day.is_quiet(15)
    assert not NewsPrefs(quiet=False).is_quiet(3)
    assert next_choice(COUNT_CHOICES, 5) == 7 and next_choice(COUNT_CHOICES, 10) == 3 and next_choice(COUNT_CHOICES, 99) == 3
    assert NewsPrefs(interval_min=60).interval_label == "каждый час"
    assert NewsPrefs(interval_min=180).interval_label == "каждые 3 ч"


def test_prefs_defaults_from_env_and_persistence():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                _env_file=None,
                bot_token="1:x",
                admin_id=1,
                rss_feeds="https://a.test/rss",
                news_quiet_hours="22-7",
                news_count=3,
            )  # type: ignore[call-arg]
            store = StateStore(Path(tmp) / "state.json")
            prefs = Prefs(store, settings)
            p = prefs.news()
            assert p.feeds == ("https://a.test/rss",) and (p.quiet_from, p.quiet_to, p.count) == (22, 7, 3)
            await prefs.update_news(count=7, feeds=("https://b.test/rss",))
            reloaded = StateStore(Path(tmp) / "state.json")
            reloaded.load()
            again = Prefs(reloaded, settings).news()
            assert again.count == 7 and again.feeds == ("https://b.test/rss",) and again.quiet_from == 22
            no_quiet = Prefs(
                StateStore(Path(tmp) / "x.json"),
                Settings(_env_file=None, bot_token="1:x", admin_id=1, news_quiet_hours=""),
            ).news()  # type: ignore[call-arg]
            assert not no_quiet.quiet

    asyncio.run(scenario())


# --------------------------------------------------------------------- вывод
def test_news_screen():
    from homebot.services.db import SentNews

    text = render_news_screen(
        NewsPrefs(feeds=("https://a.test",), interval_min=120),
        [SentNews("Новость", "текст", "https://a.test/1", "https://www.a.test/rss", int(NOW.timestamp()))],
        NOW,
        KYIV,
    )
    assert "каждые 2 ч" in text and "Новость" in text and "a.test" in text
    empty = render_news_screen(NewsPrefs(feeds=()), [], None, KYIV)
    assert "Нет источников" in empty


def test_retell_one_by_one_survives_timeout_and_translates_title():
    from homebot.services.feeds import NewsItem
    from homebot.services.news import Candidate
    from homebot.services.ollama import OllamaTimeout

    class SlowAsker:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def ask(self, system, text, *, model=None, schema=None):
            self.calls.append(text)
            if schema is None:  # перевод заголовков — одним списком
                names = {"First": "Первая", "Second": "Вторая новость"}
                return "\n".join(f"{n}. {names[line.split('. ', 1)[1]]}" for n, line in enumerate(text.splitlines(), 1))
            if "Second" in text:
                raise OllamaTimeout("timeout")
            title = "Первая" if "First" in text else "Третья по-русски"
            return json.dumps({"items": [{"n": 1, "title": title, "summary": "Пересказ."}]})

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "db.sqlite")
            db.open()
            picked = [
                Candidate(NewsItem("First", "https://x/1", NOW, "text " * 400, "u1"), "f", "src"),
                Candidate(NewsItem("Second", "https://x/2", NOW, "text two", "u2"), "f", "src"),
                Candidate(NewsItem("Третья по-русски", "https://x/3", NOW, "", "u3"), "f", "src"),
            ]
            asker = SlowAsker()
            stories = await NewsService(None, db, asker).retell(picked, NewsPrefs(fetch_articles=False))  # type: ignore[arg-type]
            assert [s.title for s in stories] == ["Первая", "Вторая новость", "Третья по-русски"]
            assert [s.retold for s in stories] == [True, False, True]
            assert stories[1].text == "text two"  # без пересказа — оригинальное описание
            retell_calls = [c for c in asker.calls if "Источник:" in c]
            assert retell_calls and len(retell_calls[0]) < 1200  # длинный текст обрезан перед моделью
            db.close()

    asyncio.run(scenario())


def test_english_title_with_russian_summary_gets_translated():
    from homebot.services.feeds import NewsItem
    from homebot.services.news import Candidate

    class Asker:
        async def ask(self, system, text, *, model=None, schema=None):
            if schema is None:
                return "1. Стивен Борн изменил британское кино"
            return json.dumps(
                {
                    "items": [
                        {
                            "n": 1,
                            "title": "Stephen Bourne Changed British Cinema",
                            "summary": "Книга о чёрных актёрах британского кино переиздаётся.",
                        }
                    ]
                }
            )

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "db.sqlite")
            db.open()
            picked = [Candidate(NewsItem("Stephen Bourne Changed British Cinema", "https://x/1", NOW, "text " * 50, "u1"), "f", "src")]
            stories = await NewsService(None, db, Asker()).retell(picked, NewsPrefs(fetch_articles=False))  # type: ignore[arg-type]
            assert stories[0].title == "Стивен Борн изменил британское кино" and stories[0].retold
            assert stories[0].text.startswith("Книга")
            db.close()

    asyncio.run(scenario())


def test_truncate_feed_keeps_fresh_head_and_stays_valid():
    from homebot.services import feeds

    rss = b"<?xml version='1.0'?><rss version='2.0'><channel><title>T</title>" + b"".join(
        f"<item><title>N{i}</title><link>https://a.ua/{i}</link></item>".encode() for i in range(50)
    )  # оборвано на полуслове — как после лимита
    rss += b"<item><title>cut"
    fixed = feeds.truncate_feed(rss)
    assert fixed is not None and fixed.endswith(b"</item></channel></rss>")
    title, items = feeds.parse_feed(fixed, 10)
    assert title == "T" and len(items) == 10 and items[0].title == "N0"
    atom = b"<feed xmlns='http://www.w3.org/2005/Atom'><title>A</title><entry><title>E</title><id>1</id></entry><entry><ti"
    assert feeds.truncate_feed(atom).endswith(b"</entry></feed>")
    assert feeds.truncate_feed(b"<html>not a feed") is None
