import asyncio
import tempfile
from datetime import date
from pathlib import Path

import pytest

import homebot.services.actual as actual_module
from homebot.services.actual import (
    Account,
    ActualService,
    Category,
    Txn,
    build_transaction_body,
    parse_accounts,
    parse_categories,
    parse_transactions,
    pick_account,
    summarize,
)
from homebot.services.apps_http import AppError
from homebot.services.expense_parse import format_money, parse_expense, to_cents
from homebot.services.expenses import (
    KEY_WORDS,
    DraftBox,
    SavedBox,
    guess_category,
    persist_saved,
    remember_category,
    restore_saved,
)
from homebot.services.state import StateStore
from homebot.ui.finance_texts import render_card, render_saved


def test_parser_accepts_short_records_and_ignores_normal_text():
    cases = {
        "кофе 85": (-8500, "кофе"),
        "85 кофе": (-8500, "кофе"),
        "+5000 зарплата": (500000, "зарплата"),
        "такси 1 200": (-120000, "такси"),
        "обед в кафе 150,50 грн": (-15050, "обед в кафе"),
        "кофе 85₴": (-8500, "кофе"),
        "-300 аптека": (-30000, "аптека"),
    }
    for text, (cents, note) in cases.items():
        parsed = parse_expense(text)
        assert parsed is not None and (parsed.cents, parsed.note) == (cents, note), text
    for text in (
        "привет как дела",
        "2026",  # голое число
        "1500",
        "/start 5",
        "https://a.test 5",
        "купить хлеб молоко сыр масло яйца 5",  # слишком длинное описание
        "кофе 0",
        "заметка " + "я" * 100 + " 5",
        "",
    ):
        assert parse_expense(text) is None, text
    assert parse_expense("+5000 зарплата").is_income  # type: ignore[union-attr]
    assert to_cents("1 200,5") == 120050 and to_cents("abc") is None and to_cents("0") is None


def test_money_formatting():
    assert format_money(-120050) == "−1 200,50 ₴"
    assert format_money(8500, sign=True) == "+85 ₴"
    assert format_money(8500, "$") == "85 $"


ACCOUNTS = [
    {"id": "a1", "name": "Старый", "closed": True},
    {"id": "a2", "name": "Накопления", "offbudget": True},
    {"id": "a3", "name": "Карта"},
]
CATEGORIES = [
    {"id": "c1", "name": "Кафе"},
    {"id": "c2", "name": "Транспорт"},
    {"id": "c3", "name": "Зарплата", "is_income": True},
    {"id": "c4", "name": "Скрытая", "hidden": True},
]


def test_actual_parsing_and_account_choice():
    accounts = parse_accounts({"data": ACCOUNTS})
    assert pick_account(accounts, "").id == "a3"  # первый открытый и в бюджете
    assert pick_account(accounts, "накопления").id == "a2"
    assert pick_account(accounts, "a1") is None  # закрытый не берём
    assert pick_account([], "") is None
    cats = parse_categories(ACCOUNTS and CATEGORIES)  # голый список тоже допустим
    assert [c.name for c in cats] == ["Кафе", "Транспорт", "Зарплата", "Скрытая"] and cats[2].is_income
    with pytest.raises(AppError):
        parse_accounts({"data": "oops"})
    txns = parse_transactions(
        {"data": [{"date": "2026-10-05", "amount": -8500, "category": "c1"}, {"amount": 5}, {"date": "x", "amount": "bad"}]}
    )
    assert len(txns) == 1 and txns[0].category_id == "c1"


def test_month_summary_and_body():
    cats = [Category("c1", "Кафе"), Category("c2", "Транспорт")]
    txns = [
        Txn("2026-10-02", -8500, "c1"),
        Txn("2026-10-03", -2000, "c1"),
        Txn("2026-10-04", -12000, "c2"),
        Txn("2026-10-05", -500, ""),
        Txn("2026-10-06", 500000, ""),
        Txn("2026-09-30", -99999, "c1"),  # прошлый месяц
        Txn("2026-10-20", -1, "c1"),  # из будущего
    ]
    summary = summarize(txns, cats, date(2026, 10, 13))
    assert summary.spent == 23000 and summary.earned == 500000 and summary.count == 5
    assert summary.by_category[0] == ("Транспорт", 12000) and ("Без категории", 500) in summary.by_category
    assert summarize([], cats, date(2026, 10, 13)).count == 0
    transfer = Txn("2026-10-07", -100000, "", "", "", transfer_id="t1")
    assert summarize([transfer], cats, date(2026, 10, 13)).spent == 0  # перевод между счетами — не расход
    body = build_transaction_body(Txn("2026-10-13", -8500, "c1", "кофе", "UNI"))
    assert body["transaction"] == {"date": "2026-10-13", "amount": -8500, "payee_name": "кофе", "category": "c1", "notes": "UNI"}
    assert "category" not in build_transaction_body(Txn("2026-10-13", -8500))["transaction"]


def test_guess_and_remember_category():
    cats = [Category("c1", "Кафе"), Category("c2", "Транспорт"), Category("c3", "Зарплата", is_income=True)]
    assert guess_category("транспорт", cats, {}, income=False) == "c2"  # по названию
    assert guess_category("кофе", cats, {"кофе": "c1"}, income=False) == "c1"  # по памяти
    assert guess_category("кофе", cats, {"кофе": "c3"}, income=False) == ""  # доходная категория для расхода не подходит
    assert guess_category("зарплата", cats, {}, income=True) == "c3"
    assert guess_category("что-то", cats, {}, income=False) == ""

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            await remember_category(store, "  Кофе ", "c1")
            await remember_category(store, "", "c1")  # пустое слово не запоминаем
            assert store.get(KEY_WORDS) == {"кофе": "c1"}

    asyncio.run(scenario())


def test_draft_box_take_put_and_expiry():
    now = [1000.0]
    box = DraftBox(clock=lambda: now[0])
    parsed = parse_expense("кофе 85")
    first = box.new(parsed, "2026-10-13", "c1")
    second = box.new(parsed, "2026-10-13")
    assert first.number != second.number and box.get(first.number) == first
    updated = box.set_category(second.number, "c2")
    assert updated is not None and updated.category_id == "c2"
    taken = box.take(first.number)
    assert taken == first and box.take(first.number) is None  # второй «Записать» ничего не найдёт
    box.put(first)
    assert box.get(first.number) is not None
    now[0] += 4000
    assert box.get(first.number) is None and box.set_category(second.number, "c1") is None  # устарели
    for _ in range(30):
        box.new(parsed, "2026-10-13")
    assert len(box._items) <= 20


def test_service_calls_use_configured_paths_and_headers():
    calls: list[tuple] = []

    async def fake_request(session, method, url, **kw):
        calls.append((method, url, kw))
        if url.endswith("/accounts"):
            return {"data": ACCOUNTS}
        if url.endswith("/categories"):
            return {"data": CATEGORIES}
        if method == "GET":
            return {"data": [{"date": "2026-10-05", "amount": -8500, "category": "c1"}]}
        return None

    original = actual_module.request
    actual_module.request = fake_request

    async def scenario():
        service = ActualService(None, "http://x:5007/", "key", "BUDGET", account="карта", encryption_password="pw")  # type: ignore[arg-type]
        account, count = await service.check()
        assert account == Account("a3", "Карта") and count == 3  # скрытая категория не считается
        await service.add(Txn("2026-10-13", -8500, "c1", "кофе", "UNI"))
        summary = await service.month(date(2026, 10, 13))
        assert summary.spent == 8500 and summary.by_category == (("Кафе", 8500),)

    try:
        asyncio.run(scenario())
    finally:
        actual_module.request = original
    _, url, kw = next(c for c in calls if c[0] == "POST")
    assert url == "http://x:5007/v1/budgets/BUDGET/accounts/a3/transactions"
    assert kw["headers"] == {"x-api-key": "key", "budget-encryption-password": "pw"}
    assert kw["json_body"]["transaction"]["amount"] == -8500
    get = next(c for c in calls if c[0] == "GET" and c[1].endswith("/transactions"))
    assert get[2]["params"] == {"since_date": "2026-10-01"}


def test_account_missing_gives_clear_error():
    async def fake_request(session, method, url, **kw):
        return {"data": ACCOUNTS}

    original = actual_module.request
    actual_module.request = fake_request
    try:
        service = ActualService(None, "http://x", "k", "B", account="нет такого")  # type: ignore[arg-type]
        try:
            asyncio.run(service.account())
        except AppError as exc:
            assert "нет такого" in str(exc)
        else:
            raise AssertionError("AppError expected")
    finally:
        actual_module.request = original


def test_expense_texts_escape_user_input():
    parsed = parse_expense("<b>кофе</b> 85")
    assert parsed is not None
    box = DraftBox()
    draft = box.new(parsed, "2026-10-13")
    card = render_card(draft, "Кафе & бары", "Карта", "₴")
    assert "&lt;b&gt;" in card and "Кафе &amp; бары" in card and "не выбрана" in render_card(draft, "", "Карта", "₴")
    assert "✅" in render_saved(draft, "", "Карта", "₴")


def test_undo_survives_restart_and_later_saves_do_not_erase_it():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            clock = [1_000_000.0]
            drafts = DraftBox(clock=lambda: clock[0])
            first = drafts.new(parse_expense("кофе 85"), "2026-10-13", category_id="c1", account_id="a1")
            before = SavedBox(clock=lambda: clock[0])
            before.add(first)
            await persist_saved(store, before)

            after_restart = SavedBox(clock=lambda: clock[0])  # новый процесс: в памяти пусто
            second = drafts.new(parse_expense("такси 120"), "2026-10-13")
            restore_saved(store, after_restart)  # так делает _save перед добавлением
            after_restart.add(second)
            await persist_saved(store, after_restart)

            third_process = SavedBox(clock=lambda: clock[0])
            restore_saved(store, third_process)
            taken = third_process.take(first.number)
            assert taken is not None and taken.parsed.cents == first.parsed.cents and taken.category_id == "c1"
            assert taken.account_id == "a1" and taken.day == "2026-10-13"
            assert third_process.take(second.number) is not None
            await persist_saved(store, third_process)
            restore_saved(store, third_process)
            assert third_process.take(first.number) is None  # отменённое не воскресает

            clock[0] += 25 * 3600  # сутки прошли — отмена недоступна
            late = SavedBox(clock=lambda: clock[0])
            restore_saved(store, late)
            assert late.take(second.number) is None
            restore_saved(None, late)  # без хранилища — просто ничего не делаем
            await persist_saved(None, late)

    asyncio.run(scenario())
