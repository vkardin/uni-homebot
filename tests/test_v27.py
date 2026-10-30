"""2.7: очередь к модели, ночной режим, финансы, стартовый экран."""

import asyncio
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import homebot.services.actual as actual_module
from homebot.services.actual import (
    Account,
    ActualService,
    BudgetCategory,
    Category,
    Txn,
    find_mine,
    parse_accounts,
    parse_month,
    parse_month_category,
    shift_month,
)
from homebot.services.apps_http import AppError, _error_detail
from homebot.services.expense_parse import parse_expense
from homebot.services.expenses import DraftBox, SavedBox
from homebot.services.home import HomeData, HomeService, short
from homebot.services.llm_queue import Priority, PriorityGate, current_priority, set_priority
from homebot.services.ollama import OllamaPreempted, OllamaService
from homebot.services.prefs import NightPrefs, Prefs, hour_in_range
from homebot.services.quiet import Importance, current_importance, set_importance, should_silence
from homebot.services.rates import Rate
from homebot.services.state import StateStore
from homebot.services.weather import WeatherInfo
from homebot.ui.finance_texts import month_title, render_budget, render_home, render_ops, render_saved, render_undone
from homebot.ui.home_text import render_home as render_start
from homebot.ui.home_text import status_line

KYIV = ZoneInfo("Europe/Kyiv")


# ---------------------------------------------------------------- очередь к модели
def test_gate_serves_by_priority_then_arrival():
    async def scenario():
        gate = PriorityGate()
        order: list[str] = []
        first = await gate.acquire(Priority.BACKGROUND)

        async def worker(name, priority):
            slot = await gate.acquire(priority)
            order.append(name)
            await asyncio.sleep(0)
            gate.release(slot)

        tasks = [
            asyncio.create_task(worker("news-2", Priority.BACKGROUND)),
            asyncio.create_task(worker("docker", Priority.ALERT)),
            asyncio.create_task(worker("chat", Priority.USER)),
            asyncio.create_task(worker("news-3", Priority.BACKGROUND)),
        ]
        await asyncio.sleep(0.01)
        assert gate.waiting == 4
        gate.release(first)
        await asyncio.gather(*tasks)
        assert order == ["chat", "docker", "news-2", "news-3"]
        assert gate.holder is None

    asyncio.run(scenario())


def test_gate_cancelled_waiter_does_not_block_queue():
    async def scenario():
        gate = PriorityGate()
        held = await gate.acquire(Priority.USER)
        waiter = asyncio.create_task(gate.acquire(Priority.USER))
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        gate.release(held)
        slot = await asyncio.wait_for(gate.acquire(Priority.BACKGROUND), 1)  # очередь не застряла
        gate.release(slot)
        assert gate.holder is None

    asyncio.run(scenario())


def test_priority_is_per_task_context():
    async def scenario():
        async def background():
            set_priority(Priority.BACKGROUND)
            return current_priority()

        assert await asyncio.create_task(background()) == Priority.BACKGROUND
        assert current_priority() == Priority.USER  # чужая задача на нас не повлияла

    asyncio.run(scenario())


class SlowOllama(OllamaService):
    """Настоящая очередь и прерывание, но вместо HTTP — ожидание."""

    def __init__(self):
        self._gate = PriorityGate()
        self.started: list[str] = []

    async def generate(self, name: str, seconds: float) -> str:
        slot = await self._gate.acquire()
        try:
            return await self._preemptable(slot, self._work(name, seconds))
        finally:
            self._gate.release(slot)

    async def _work(self, name, seconds):
        self.started.append(name)
        await asyncio.sleep(seconds)
        return name


def test_user_request_preempts_background_generation():
    async def scenario():
        ollama = SlowOllama()

        async def news():
            set_priority(Priority.BACKGROUND)
            return await ollama.generate("news", 60)

        news_task = asyncio.create_task(news())
        await asyncio.sleep(0.01)
        answer = await asyncio.wait_for(ollama.generate("chat", 0.01), 2)  # не ждём минуту
        assert answer == "chat"
        result = await asyncio.gather(news_task, return_exceptions=True)
        assert isinstance(result[0], OllamaPreempted)
        assert ollama.gate.holder is None

    asyncio.run(scenario())


def test_alert_does_not_preempt_and_outer_cancel_still_cancels():
    async def scenario():
        ollama = SlowOllama()

        async def news():
            set_priority(Priority.BACKGROUND)
            return await ollama.generate("news", 0.05)

        async def docker():
            set_priority(Priority.ALERT)
            return await ollama.generate("docker", 0.01)

        results = await asyncio.gather(asyncio.create_task(news()), asyncio.create_task(docker()))
        assert results == ["news", "docker"]  # разбор Docker вежливо ждёт

        task = asyncio.create_task(ollama.generate("long", 60))
        await asyncio.sleep(0.01)
        task.cancel()
        assert isinstance((await asyncio.gather(task, return_exceptions=True))[0], asyncio.CancelledError)
        assert ollama.gate.holder is None

    asyncio.run(scenario())


# ------------------------------------------------------------------ ночной режим
def test_night_ranges_and_silence_rules():
    assert hour_in_range(23, 23, 8) and hour_in_range(3, 23, 8) and not hour_in_range(8, 23, 8)
    assert hour_in_range(1, 0, 8) and not hour_in_range(5, 5, 5)
    night = NightPrefs(enabled=True, start=23, end=8)
    assert should_silence(Importance.NORMAL, night, 2)
    assert not should_silence(Importance.CRITICAL, night, 2)  # тревоги и диски — со звуком
    assert not should_silence(Importance.INTERACTIVE, night, 2)  # ответ на ваше сообщение — со звуком
    assert not should_silence(Importance.NORMAL, night, 14)
    assert not should_silence(Importance.NORMAL, NightPrefs(enabled=False), 2)
    assert night.label == "23:00–08:00" and NightPrefs(enabled=False).label == "выключен"

    async def scenario():
        async def job():
            set_importance(Importance.NORMAL)
            return current_importance()

        assert await asyncio.create_task(job()) == Importance.NORMAL
        assert current_importance() == Importance.INTERACTIVE

    asyncio.run(scenario())


def test_night_prefs_default_from_news_and_persist():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(news_quiet_hours="22-7", news_enabled=True, news_interval_min=60, news_count=5, feeds=[])
            prefs = Prefs(StateStore(Path(tmp) / "s.json"), settings)  # type: ignore[arg-type]
            assert prefs.night() == NightPrefs(True, 22, 7)  # как тихие часы новостей
            await prefs.update_night(enabled=False)
            await prefs.update_night(start=0, end=8)
            again = Prefs(StateStore(Path(tmp) / "s.json"), settings)  # type: ignore[arg-type]
            again._store.load()
            assert again.night() == NightPrefs(False, 0, 8)

    asyncio.run(scenario())


# ------------------------------------------------------------------- финансы
MONTH = {
    "data": {
        "month": "2026-10",
        "totalIncome": 2500000,
        "totalSpent": -820000,
        "totalBudgeted": 2000000,
        "toBudget": 150000,
        "categoryGroups": [
            {
                "id": "g1",
                "name": "Повседневное",
                "is_income": False,
                "categories": [
                    {"id": "c1", "name": "Кафе", "budgeted": 100000, "spent": -130000, "balance": -30000},
                    {"id": "c2", "name": "Продукты", "budgeted": 500000, "spent": -420000, "balance": 80000},
                    {"id": "c3", "name": "Транспорт", "budgeted": 0, "spent": -45000, "balance": -45000},
                    {"id": "c4", "name": "Пусто", "budgeted": 0, "spent": 0, "balance": 0},
                    {"id": "c5", "name": "Скрытая", "hidden": True, "budgeted": 1, "spent": -1, "balance": 0},
                ],
            },
            {"id": "g2", "name": "Доход", "is_income": True, "categories": [{"id": "i1", "name": "Зарплата", "received": 2500000}]},
            {"id": "g3", "name": "Архив", "hidden": True, "categories": [{"id": "x", "name": "Старое", "spent": -5}]},
        ],
    }
}


def test_parse_budget_month_and_render():
    month = parse_month(MONTH)
    assert month.spent == 820000 and month.income == 2500000 and month.to_budget == 150000
    assert [c.id for c in month.categories] == ["c1", "c2", "c3", "c4", "i1"]  # скрытое не показываем
    assert month.categories[-1].is_income and month.categories[0].overspent
    text = render_budget(month, "₴")
    assert text.index("Кафе") < text.index("Продукты")  # перерасход — первым
    assert "🔴 Кафе" in text and "🟡 Продукты" in text and "Пусто" not in text and "Зарплата" not in text
    assert "октябрь 2026" in text and "К распределению" in text
    try:
        parse_month({"data": []})
    except AppError:
        pass
    else:
        raise AssertionError("AppError expected")
    assert parse_month_category({"data": {"id": "c1", "name": "Кафе", "budgeted": 5, "balance": -1}}).balance == -1  # type: ignore[union-attr]
    assert parse_month_category({"data": None}) is None
    assert (
        shift_month("2026-01", -1) == "2025-12" and shift_month("2026-10", 0) == "2026-10" and shift_month("2025-12", 1) == "2026-01"
    )
    assert month_title("2026-03", capital=True) == "Март 2026"


def test_home_screen_text():
    accounts = parse_accounts(
        {
            "data": [
                {"id": "a1", "name": "Карта", "workingBalance": 1234000},
                {"id": "a2", "name": "Наличные", "workingBalance": 80000},
                {"id": "a3", "name": "Накопления", "offbudget": True, "workingBalance": 5000000},
                {"id": "a4", "name": "Старый", "closed": True, "workingBalance": 1},
            ]
        }
    )
    assert accounts[0].balance == 1234000 and parse_accounts({"data": [{"id": "z"}]})[0].balance is None
    text = render_home(accounts, parse_month(MONTH), "Карта", "₴")
    assert "Всего в бюджете: <b>13 140 ₴</b>" in text and "Накопления" in text and "Старый" not in text
    assert "Октябрь 2026: расход <b>8 200 ₴</b>" in text and "Перерасход: Транспорт 450 ₴, Кафе 300 ₴" in text
    assert "Записываю на: <b>Карта</b>" in text
    assert "недоступен: нет связи" in render_home(accounts, None, "Карта", "₴", month_error="нет связи")


def test_ops_text_and_find_mine():
    txns = [
        Txn("2026-10-13", -8500, "c1", "p1", "UNI", id="t1", account_id="a1"),
        Txn("2026-10-12", 2500000, "i1", "p2", "", id="t2", account_id="a2"),
        Txn("2026-10-11", -50000, "", "p3", "", id="t3", account_id="a1", transfer_id="t4"),
    ]
    text = render_ops(
        txns,
        "₴",
        payees={"p1": "Кофейня <Зерно>", "p2": "Работа", "p3": "Наличные"},
        categories=[Category("c1", "Кафе"), Category("i1", "Зарплата")],
        accounts=[Account("a1", "Карта"), Account("a2", "Наличные")],
    )
    assert "1. 13.10 · −85 ₴ · Кофейня &lt;Зерно&gt; · <i>Кафе</i> · Карта" in text
    assert "↔️ 500 ₴ · перевод: Наличные" in text and "+25 000 ₴" in text
    assert "За последний месяц операций нет" in render_ops([], "₴", payees={}, categories=[], accounts=[])

    wanted = Txn("2026-10-13", -8500, "c1", "кофе", "UNI")
    assert find_mine(txns, wanted).id == "t1"  # type: ignore[union-attr]
    assert find_mine([*txns[:1]], Txn("2026-10-13", -8500, "c2")) is None  # другая категория
    foreign = Txn("2026-10-13", -8500, "c1", "", "из банка", id="b1")
    assert find_mine([foreign], wanted) is None  # чужие (импорт из банка) не трогаем


def test_saved_text_shows_remaining_budget():
    box = DraftBox()
    draft = box.new(parse_expense("кофе 85"), "2026-10-13", "c1", "a1")  # type: ignore[arg-type]
    ok = render_saved(draft, "Кафе", "Карта", "₴", BudgetCategory("c1", "Кафе", "", budgeted=100000, spent=-20000, balance=80000))
    assert "Осталось в «Кафе»: <b>800 ₴</b> из 1 000 ₴" in ok
    over = render_saved(draft, "Кафе", "Карта", "₴", BudgetCategory("c1", "Кафе", "", budgeted=100000, balance=-500))
    assert "перерасход 5 ₴" in over
    plain = render_saved(draft, "Кафе", "Карта", "₴", BudgetCategory("c1", "Кафе", "", budgeted=0, balance=0))
    assert "Осталось" not in plain and "перерасход" not in plain
    assert "↩️ Отменено" in render_undone(draft, "₴", True) and "не нашёл" in render_undone(draft, "₴", False)
    assert draft.to_txn().notes == "UNI" and draft.account_id == "a1"


def test_bare_amount_allowed_only_in_entry_mode():
    assert parse_expense("85") is None
    bare = parse_expense("85", allow_bare=True)
    assert bare is not None and bare.cents == -8500 and bare.note == ""
    assert parse_expense("1 200,50", allow_bare=True).cents == -120050  # type: ignore[union-attr]


def test_draft_account_and_saved_box_ttl():
    now = [1000.0]
    box = DraftBox(clock=lambda: now[0])
    draft = box.new(parse_expense("кофе 85"), "2026-10-13")  # type: ignore[arg-type]
    assert box.set_account(draft.number, "a2").account_id == "a2"  # type: ignore[union-attr]
    assert box.set_account(999999, "a2") is None
    saved = SavedBox(clock=lambda: now[0])
    saved.add(draft)
    assert saved.take(draft.number) == draft and saved.take(draft.number) is None  # второе «Отменить» — пусто
    saved.put_back(draft)
    now[0] += 25 * 3600
    assert saved.take(draft.number) is None  # сутки прошли


def test_service_recent_undo_delete_and_month(monkeypatch=None):
    calls: list[tuple] = []
    store_txns = {
        "a1": [{"id": "t1", "account": "a1", "date": "2026-10-13", "amount": -8500, "category": "c1", "notes": "UNI"}],
        "a2": [{"id": "t2", "date": "2026-10-12", "amount": 100, "notes": ""}],
    }

    async def fake_request(session, method, url, **kw):
        calls.append((method, url, kw))
        if method == "DELETE":
            return {"message": "Transaction deleted"}
        if "/months/2026-10/categories/c1" in url:
            return {"data": {"id": "c1", "name": "Кафе", "budgeted": 100, "balance": 10}}
        if url.endswith("/months/2026-10"):
            return MONTH
        if "/accounts/" in url and url.endswith("/transactions"):
            return {"data": store_txns[url.split("/accounts/")[1].split("/")[0]]}
        if url.endswith("/accounts"):
            return {"data": [{"id": "a1", "name": "Карта"}]}
        raise AssertionError(url)

    original = actual_module.request
    actual_module.request = fake_request
    try:

        async def scenario():
            service = ActualService(None, "http://x:5007", "k", "B")  # type: ignore[arg-type]
            rows = await service.recent([Account("a1", "Карта"), Account("a2", "Наличные")], date(2026, 9, 12))
            assert [t.id for t in rows] == ["t1", "t2"] and rows[1].account_id == "a2"  # счёт подставлен
            assert await service.undo(Txn("2026-10-13", -8500, "c1", "кофе", "UNI"), "a1")
            assert not await service.undo(Txn("2026-10-13", -9900, "c1", "кофе", "UNI"), "a1")
            assert (await service.budget_month("2026-10")).spent == 820000
            assert (await service.month_category("2026-10", "c1")).balance == 10  # type: ignore[union-attr]
            await service.delete("t2")

        asyncio.run(scenario())
    finally:
        actual_module.request = original
    deletes = [c[1] for c in calls if c[0] == "DELETE"]
    assert deletes == ["http://x:5007/v1/budgets/B/transactions/t1", "http://x:5007/v1/budgets/B/transactions/t2"]
    undo_get = next(
        c for c in calls if c[0] == "GET" and "since_date" in (c[2].get("params") or {}) and "until_date" in c[2]["params"]
    )
    assert undo_get[2]["params"] == {"since_date": "2026-10-13", "until_date": "2026-10-14"}


def test_error_detail_from_wrapper_body():
    assert _error_detail(b'{"error": "Account not found"}') == ": Account not found"
    assert _error_detail(b"<html>oops</html>") == "" and _error_detail(b'{"error": ""}') == "" and _error_detail(b"[1]") == ""


# --------------------------------------------------------------- стартовый экран
class FakeDb:
    def __init__(self, rows=(), news=()):
        self.rows, self.news = list(rows), list(news)

    async def metrics_since(self, since):
        return self.rows

    async def recent_sent_news(self, limit):
        return self.news[:limit]


def test_home_service_caches_and_survives_failures():
    async def scenario():
        clock = [0.0]
        calls = {"weather": 0, "rates": 0}
        broken = {"weather": False}

        async def weather():
            calls["weather"] += 1
            if broken["weather"]:
                raise ConnectionError("down")
            return WeatherInfo(12.4, 10, 3, 2, 15, 7, 20)

        async def rates():
            calls["rates"] += 1
            return [Rate("USD", 41.2534, ""), Rate("EUR", 47.9, "")]

        async def spent():
            return 820000

        db = FakeDb(
            rows=[SimpleNamespace(cpu=7.2, ram=38.4)],
            news=[SimpleNamespace(title="Нацбанк сохранил ставку " * 6), SimpleNamespace(title="")],
        )
        service = HomeService(
            session=None, db=db, system=None, coords=(47.9, 33.4), currencies=["USD", "EUR"],
            month_spent=spent, clock=lambda: clock[0], fetch_weather=weather, fetch_rates_fn=rates,
        )  # fmt: skip
        data = await service.data()
        assert data.weather is not None and len(data.rates) == 2 and data.cpu == 7.2 and data.month_spent == 820000
        assert len(data.news) == 1  # пустые заголовки не показываем
        await service.data()
        assert calls == {"weather": 1, "rates": 1}  # кэш
        clock[0] += 16 * 60
        broken["weather"] = True
        data = await service.data()
        assert calls["weather"] == 2 and data.weather is not None  # источник упал — показываем прошлое
        clock[0] += 4 * 3600
        assert (await service.data()).weather is None  # слишком старое — лучше не показывать

        async def slow():
            await asyncio.sleep(10)

        lazy = HomeService(
            session=None, db=FakeDb(), system=SimpleNamespace(snapshot=None), coords=(1.0, 2.0), currencies=[],
            fetch_weather=slow,
        )  # fmt: skip
        started = asyncio.get_running_loop().time()
        data = await lazy.data(wait=0.05)
        assert asyncio.get_running_loop().time() - started < 1 and data.weather is None and data.cpu is None

    asyncio.run(scenario())


def test_start_screen_text():
    now = datetime(2026, 10, 13, 15, 30, tzinfo=UTC)  # 18:30 в Киеве
    data = HomeData(
        WeatherInfo(12.4, 10, 3, 2, 15, 7, 20), (Rate("USD", 41.2534, ""), Rate("PLN", 10.3, "")), 7.2, 38.4,
        ("Очень длинная новость про <теги> " * 4, "Короткая"), 820000,
    )  # fmt: skip
    text = render_start(now, KYIV, data)
    assert text.startswith("🤖 <b>UNI на связи</b> · Добрый вечер!")
    assert "⛅ +12° · 💵 41,25 · 🇵🇱 10,30 · 🖥 CPU 7% · RAM 38%" in text
    assert "&lt;теги&gt;" in text and "…" in text and "🗞 Короткая" in text
    assert "💰 Октябрь: расход 8 200 ₴" in text and text.endswith("Чем займёмся?")
    assert "Новости каждый час" in render_start(now, KYIV, HomeData())  # пусто — рассказываем, что умею
    assert "Новости каждый час" in render_start(now, KYIV, None)
    assert status_line(HomeData(cpu=5.0)) == "🖥 CPU 5%"
    assert short("слово " * 30, 20).endswith("…") and len(short("слово " * 30, 20)) <= 20


def test_whats_new_shown_once_after_update():
    from homebot.changelog import WHATS_NEW, whats_new

    text = whats_new("2.8.3", "3.0.0")
    assert "Что нового в 3.0.0" in text and all(line in text for line in WHATS_NEW["3.0.0"])
    assert whats_new(None, "3.0.0") == ""  # первая установка — без истории изменений
    assert whats_new("3.0.0", "3.0.0") == ""  # перезапуск без обновления
    assert whats_new("3.0.0", "9.9.9") == ""  # версия без записи


def test_doctor_collects_checks_feeds_and_renders():
    from homebot.services.doctor import FAIL, OK, WARN, Check, collect, guard, guard_many, ollama_check
    from homebot.ui.extra_texts import render_doctor

    class Ollama:
        active_model = "qwen3.5:4b"

        async def list_models(self):
            return ["qwen3.5:4b", "qwen3:1.7b"]

    async def fetch(url):
        if "dead" in url:
            raise ConnectionError("HTTP 404")
        full = SimpleNamespace(content="<p>" + "текст " * 200 + "</p>")
        short = SimpleNamespace(content="")
        return ("Полная лента", [full, short]) if "full" in url else ("Анонсы", [short])

    async def slow():
        await asyncio.sleep(10)

    async def broken():
        raise RuntimeError("сломалось")

    async def scenario():
        checks = [
            guard("Медленный", slow(), limit=0.05),
            guard("Сломанный", broken()),
            guard_many("Ollama", ollama_check(Ollama(), news_model="", translate_model="qwen3:1.7b", machine_url="")),
            guard_many("Ollama", ollama_check(Ollama(), news_model="gone:1b", translate_model="", machine_url="")),
        ]
        report = await collect(checks, ["https://full.test/rss", "https://short.test/rss", "https://dead.test/rss"], fetch, ["Docker"])
        by_name = {(c.name, c.detail): c.status for c in report.checks}
        assert by_name[("Медленный", "нет ответа за 0 с")] == FAIL and ("Сломанный", "сломалось") in by_name
        assert {c.status for c in report.checks if c.name == "Перевод"} == {OK, WARN}  # лёгкая выбрана / не выбрана
        assert any(c.name == "Модель новостей" and c.status == FAIL for c in report.checks)
        assert report.feeds[2].error == "HTTP 404" and report.feeds[0].items == 2
        text = render_doctor(report, "3.0.0")
        assert "✅ Полная лента — 2 новостей" in text and "✅ Анонсы — 1 новостей" in text
        assert "❌ dead.test — HTTP 404" in text and "Не подключены: Docker" in text and "есть что поправить" in text
        ok = await collect([guard("Telegram", _ok())], [], None, [])
        assert "все системы в порядке" in render_doctor(ok, "3.0.0")

    async def _ok():
        return Check(OK, "Telegram", "@uni_bot")

    asyncio.run(scenario())
