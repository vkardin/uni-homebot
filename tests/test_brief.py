import asyncio
import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest

from homebot.jobs.hourly_news import HourlyNewsJob
from homebot.services.brief import KEY_LAST_RUN, KEY_MESSAGE, Brief, BriefService
from homebot.services.db import Database, SentNews
from homebot.services.digest import Digest
from homebot.services.news import NewsBatch, NewsService, Story, is_russian, mostly_foreign
from homebot.services.prefs import MODE_NEW, NewsPrefs
from homebot.services.rates import Rate
from homebot.services.state import StateStore
from homebot.services.system import DiskInfo, SystemSnapshot
from homebot.services.units import UnitStatus
from homebot.services.weather import WeatherInfo
from homebot.ui.texts import render_brief

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)  # 13:00 по Киеву


def report() -> Digest:
    snap = SystemSnapshot(
        taken_at=NOW, cpu_percent=5, cpu_threads=4, load_avg=None, ram_total=1, ram_used=1, ram_percent=57,
        swap_total=0, swap_used=0, swap_percent=0, disks=(DiskInfo("/", 100, 9, 9.0),), temperature=None, uptime_sec=3600,
    )  # fmt: skip
    return Digest(
        created_at=NOW,
        weather_label="Кривой Рог",
        system=snap,
        services=[UnitStatus("ssh", "loaded", "active", "running")],
        weather=WeatherInfo(14, 12, 3, 3, 15, 12, 95),
        rates=[Rate("USD", 41.25, "13.10.2026")],
    )


def story(i: int) -> Story:
    return Story(f"k{i}", f"Новость {i}", f"Пересказ новости {i}.", f"https://x.test/{i}", "x.test", NOW, True)


# ------------------------------------------------------------------- вывод
def test_brief_order_weather_news_server_and_no_apps():
    brief = Brief(
        NOW, report(), [story(1), story(2)], [SentNews("Утренняя", "", "https://x.test/m", "f", int(NOW.timestamp()) - 3600)]
    )
    brief.next_at = NOW + timedelta(hours=1)
    text = render_brief(brief, KYIV)
    order = [text.index(marker) for marker in ("Кривой Рог", "USD", "Новости за час", "Новость 1", "Ранее сегодня", "Сервер")]
    assert order == sorted(order)
    assert "13:00" in text and "следующее в 14:00" in text and "🐳" not in text and "AdGuard" not in text


def test_brief_without_new_news_and_budget():
    quiet = render_brief(Brief(NOW, report(), [], [], collected=True), KYIV)
    assert "нового нет" in quiet
    huge = [Story(f"k{i}", "Заголовок " * 8, "очень длинный пересказ " * 40, "https://x.test", "x", NOW, True) for i in range(10)]
    earlier = [SentNews("Ранняя новость " * 5, "", "https://x.test/e", "f", int(NOW.timestamp())) for _ in range(30)]
    text = render_brief(Brief(NOW, report(), huge, earlier), KYIV)
    assert len(text) <= 4096 and "Сервер" in text  # сервер не обрезается, сокращаются новости


# ------------------------------------------------------------- проверка языка
def test_language_helpers():
    assert is_russian("Valve представила новую консоль Steam Machine")
    assert not is_russian("Valve announces a new Steam Machine console")
    uk = "Ші-моделі можуть навчитися самостійно поширюватися через телевізори"
    assert not is_russian(uk)  # украинский по умолчанию переводится на русский
    from homebot.services.lang import set_translate_ukrainian

    set_translate_ukrainian(False)
    try:
        assert is_russian(uk)  # TRANSLATE_UKRAINIAN=false — оставляем как есть
    finally:
        set_translate_ukrainian(True)
    assert is_russian("Укрзалізниця запустила новый поезд в Европу и добавила вагоны")  # одно украинское название — это русский
    assert mostly_foreign({0: ("English title", "English text"), 1: ("Заголовок", "Текст")}) is False
    assert mostly_foreign({0: ("English title", "English text"), 1: ("Another", "text")}) is True


class LangAsker:
    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str | None]] = []

    async def ask(self, system, text, *, model=None, schema=None):
        if schema is None:
            return ""  # перевод заголовков: в этих тестах не нужен — останется оригинал
        self.calls.append((system, model))
        return self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]


def _answer(title: str, summary: str) -> str:
    return json.dumps({"items": [{"n": 1, "title": title, "summary": summary}]})


def test_retell_retries_when_model_answers_in_english_and_uses_news_model():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "db.sqlite")
            db.open()
            from homebot.services.feeds import NewsItem
            from homebot.services.news import Candidate

            picked = [Candidate(NewsItem("Original title", "https://x.test/1", NOW, "summary " * 30, "u1"), "f", "src")]
            asker = LangAsker([_answer("English title", "English summary"), _answer("Русский заголовок", "Русский пересказ.")])
            service = NewsService(None, db, asker)  # type: ignore[arg-type]
            stories = await service.retell(picked, NewsPrefs(model="qwen3.5:4b", fetch_articles=False))
            assert stories[0].title == "Русский заголовок" and stories[0].retold
            assert len(asker.calls) == 2 and "ТОЛЬКО на русском" in asker.calls[1][0]
            assert asker.calls[0][1] == "qwen3.5:4b"  # своя модель новостей, а не модель чата

            stubborn = LangAsker([_answer("English title", "English summary")])
            stories = await NewsService(None, db, stubborn).retell(picked, NewsPrefs(fetch_articles=False))  # type: ignore[arg-type]
            assert stories[0].title == "Original title" and not stories[0].retold  # честный оригинал вместо английского «пересказа»
            assert stubborn.calls[0][1] is None  # модель не выбрана — как в чате
            db.close()

    asyncio.run(scenario())


# ----------------------------------------------------------------- доставка
class FakeBot:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edited: list[tuple[int, str]] = []
        self.edit_error: str | None = None
        self._next_id = 100

    async def send_message(self, chat_id, text, reply_markup=None):
        self._next_id += 1
        self.sent.append(text)
        return SimpleNamespace(message_id=self._next_id)

    async def edit_message_text(self, text, chat_id, message_id, reply_markup=None):
        if self.edit_error:
            raise TelegramBadRequest(method=None, message=self.edit_error)  # type: ignore[arg-type]
        self.edited.append((message_id, text))


class FakeDigest:
    async def build(self):
        return report()


class FakeNews:
    def __init__(self) -> None:
        self.stories: list[Story] = []
        self.windows: list[timedelta] = []
        self.marked: list[str] = []

    async def run(self, prefs, *, now, window):
        self.windows.append(window)
        return NewsBatch(list(self.stories), len(self.stories), [])

    async def mark_sent(self, stories):
        self.marked += [s.key for s in stories]


class StaticPrefs:
    def __init__(self, p: NewsPrefs) -> None:
        self.p = p

    def news(self):
        return self.p


def _setup(tmp: str, prefs: NewsPrefs):
    db = Database(Path(tmp) / "db.sqlite")
    db.open()
    store = StateStore(Path(tmp) / "state.json")
    news = FakeNews()
    briefs = BriefService(digest=FakeDigest(), news=news, db=db, prefs=StaticPrefs(prefs), store=store, tz=KYIV)  # type: ignore[arg-type]
    return db, store, news, briefs


async def _cycle(briefs: BriefService, bot: FakeBot, now: datetime, prefs: NewsPrefs, **kw) -> bool:
    brief = await briefs.build(now)
    return await briefs.deliver(bot, 1, render_brief(brief, KYIV), None, brief, prefs=prefs, **kw)  # type: ignore[arg-type]


def test_day_mode_one_message_per_day_updated_in_place():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            prefs = NewsPrefs(feeds=("https://a.test",))
            db, store, news, briefs = _setup(tmp, prefs)
            bot = FakeBot()

            news.stories = [story(1)]
            assert await _cycle(briefs, bot, NOW, prefs)
            assert len(bot.sent) == 1 and bot.edited == [] and news.marked == ["k1"]
            first_id = store.get(KEY_MESSAGE)["id"]
            assert store.get(KEY_LAST_RUN) == int(NOW.timestamp())

            news.stories = []  # в следующий час новостей нет — всё равно тихо обновляем погоду/сервер
            assert await _cycle(briefs, bot, NOW + timedelta(hours=1), prefs)
            assert len(bot.sent) == 1 and bot.edited[-1][0] == first_id

            bot.edit_error = "Bad Request: message is not modified"
            assert await _cycle(briefs, bot, NOW + timedelta(hours=2), prefs)
            assert len(bot.sent) == 1

            bot.edit_error = "Bad Request: message to edit not found"  # пользователь удалил сводку
            assert await _cycle(briefs, bot, NOW + timedelta(hours=3), prefs)
            assert len(bot.sent) == 2 and store.get(KEY_MESSAGE)["id"] != first_id

            bot.edit_error = None
            assert await _cycle(briefs, bot, NOW + timedelta(days=1), prefs)  # новый день — новое сообщение
            assert len(bot.sent) == 3
            db.close()

    asyncio.run(scenario())


def test_new_mode_sends_only_with_news_and_manual_reuses_message():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            prefs = NewsPrefs(feeds=("https://a.test",), mode=MODE_NEW)
            db, store, news, briefs = _setup(tmp, prefs)
            bot = FakeBot()
            assert not await _cycle(briefs, bot, NOW, prefs)  # нет новостей — не шумим
            news.stories = [story(1)]
            assert await _cycle(briefs, bot, NOW + timedelta(hours=1), prefs)
            assert await _cycle(briefs, bot, NOW + timedelta(hours=2), prefs)
            assert len(bot.sent) == 2 and bot.edited == []

            # «⚡ Обновить сейчас»: сообщение с кнопкой само становится сводкой
            assert await _cycle(briefs, bot, NOW + timedelta(hours=3), prefs, reuse_message_id=555)
            assert bot.edited[-1][0] == 555 and store.get(KEY_MESSAGE)["id"] == 555
            db.close()

    asyncio.run(scenario())


def test_news_not_marked_sent_if_delivery_failed():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            prefs = NewsPrefs(feeds=("https://a.test",))
            db, _store, news, briefs = _setup(tmp, prefs)

            class DownBot(FakeBot):
                async def send_message(self, chat_id, text, reply_markup=None):
                    from aiogram.exceptions import TelegramAPIError

                    raise TelegramAPIError(method=None, message="offline")  # type: ignore[arg-type]

            news.stories = [story(1)]
            assert not await _cycle(briefs, DownBot(), NOW, prefs)
            assert news.marked == []
            db.close()

    asyncio.run(scenario())


def test_hourly_job_quiet_and_window():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            prefs = NewsPrefs(feeds=("https://a.test",), quiet=True, quiet_from=23, quiet_to=8)
            db, _store, news, briefs = _setup(tmp, prefs)
            bot = FakeBot()
            job = HourlyNewsJob(bot=bot, admin_id=1, briefs=briefs, prefs=StaticPrefs(prefs), tz=KYIV, markup=lambda: None)  # type: ignore[arg-type]
            evening = datetime(2026, 10, 13, 22, 0, tzinfo=KYIV)
            assert await job.tick(evening)
            assert not await job.tick(datetime(2026, 10, 14, 3, 0, tzinfo=KYIV))  # тихие часы
            assert await job.tick(datetime(2026, 10, 14, 8, 0, tzinfo=KYIV))
            assert news.windows[-1] == timedelta(hours=10)  # утром — всё с вечера
            assert len(bot.sent) == 2  # вечер 13-го и утро 14-го — разные дни, разные сообщения

            off = HourlyNewsJob(
                bot=bot, admin_id=1, briefs=briefs, prefs=StaticPrefs(NewsPrefs(enabled=False)), tz=KYIV, markup=lambda: None
            )  # type: ignore[arg-type]
            assert not await off.tick(evening)
            db.close()

    asyncio.run(scenario())


def test_sent_news_since_today():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "db.sqlite")
            db.open()
            from homebot.services.db import SeenNews

            await db.mark_news_seen([SeenNews("a", "f", "A", None, None), SeenNews("b", "f", "B", None, None)], 1)
            await db.mark_news_sent([("a", "Вчера", "")], 100)
            await db.mark_news_sent([("b", "Сегодня", "")], 200)
            assert [n.title for n in await db.sent_news_since(150)] == ["Сегодня"]
            db.close()

    asyncio.run(scenario())


def test_ukrainian_detection_cases():
    from homebot.services.lang import is_ukrainian, needs_russian

    assert is_ukrainian("Украину обрали членом Ради ООН з прав людини на 2027–2029 годів")  # одна буква «і», но «з»
    assert is_ukrainian("ЄС відкрив кластери 2 та 3 у переговорах про вступ України")
    assert is_ukrainian("Окупанти вдарили безпілотниками по центру Павлограда: загинула людина")
    assert not is_ukrainian("Укрзалізниця запустила новый поезд в Европу и добавила вагоны")
    assert not is_ukrainian("Как сообщили в Укрзалізниці, Інтерсіті и Ощадбанке, в стране " + "обычный текст " * 30)
    assert not is_ukrainian("Нацбанк сохранил учётную ставку на уровне 15,5%")
    assert needs_russian("Josh Brolin has a list of 74 movies") and not needs_russian("123 456") and not needs_russian("Курс доллара")
