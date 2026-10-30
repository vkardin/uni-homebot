"""👥 Доступ: сервис заявок и прав, проверка в middleware, личные данные гостей в БД."""

import asyncio
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from aiogram.enums import ChatType

from homebot.middlewares import AdminOnlyMiddleware
from homebot.services.access import APPROVED, BLOCKED, DEFAULT_PERMS, MAX_PENDING, PENDING, AccessService
from homebot.services.db import Database
from homebot.services.state import StateStore

ADMIN = 1001

try:  # локально без aiogram тесты идут на заглушках: клавиатуры подменяем, в обычном окружении импорт настоящий
    import homebot.ui.keyboards  # noqa: F401
except ImportError:
    import sys
    import types

    stub = types.ModuleType("homebot.ui.keyboards")
    stub.access_request_kb = lambda uid: None
    sys.modules["homebot.ui.keyboards"] = stub


def make_access(tmp: str) -> AccessService:
    store = StateStore(Path(tmp) / "state.json")
    store.load()
    return AccessService(store, ADMIN)


def run(coro):
    return asyncio.run(coro)


def test_new_person_becomes_pending_and_is_created_once():
    with tempfile.TemporaryDirectory() as tmp:
        access = make_access(tmp)
        person, created = run(access.register(5, "Вася\n<b>", "vasya"))
        assert created and person.status == PENDING and person.perms == DEFAULT_PERMS
        assert "\n" not in person.name
        again, created = run(access.register(5, "Другое имя", None))
        assert not created and again.name == person.name
        assert not person.can("notes")  # пока не принят — ничего нельзя


def test_approve_toggle_block_remove_and_persistence():
    with tempfile.TemporaryDirectory() as tmp:
        access = make_access(tmp)
        run(access.register(5, "Вася", "vasya"))
        person = run(access.set_status(5, APPROVED))
        assert person.can("notes") and person.can("p2p")
        person = run(access.toggle(5, "p2p"))
        assert not person.can("p2p") and person.can("notes")
        assert run(access.toggle(5, "nonsense")) is None  # неизвестной функции не бывает
        assert run(access.toggle(404, "notes")) is None
        assert run(access.set_status(5, "weird")) is None
        # то же самое видно после перезапуска
        restored = make_access(tmp)
        assert restored.get(5).perms == person.perms
        assert run(access.set_status(5, BLOCKED)).status == BLOCKED
        assert not access.get(5).can("notes")
        assert run(access.remove(5)) and access.get(5) is None and not run(access.remove(5))


def test_pending_cap_protects_from_flood():
    with tempfile.TemporaryDirectory() as tmp:
        access = make_access(tmp)
        for uid in range(MAX_PENDING):
            assert run(access.register(1000 + uid, f"u{uid}", None))[0] is not None
        assert run(access.register(5000, "late", None))[0] is None


def test_corrupt_records_are_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(Path(tmp) / "state.json")
        store.load()
        run(store.set("access", {"1": "oops", "x": {"status": "approved"}, "7": {"status": "approved", "perms": ["notes", "root"]}}))
        access = AccessService(store, ADMIN)
        people = access.people()
        assert [p.id for p in people] == [7] and people[0].perms == ("notes",)


def test_reminder_politeness_is_rate_limited():
    with tempfile.TemporaryDirectory() as tmp:
        access = make_access(tmp)
        assert access.should_remind(5) and not access.should_remind(5)
        assert access.should_remind(6)


# ------------------------------------------------------------------ middleware
class Bot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


class Probe:
    def __init__(self) -> None:
        self.guests: list = []

    async def __call__(self, event, data):
        self.guests.append(data.get("guest"))
        return "handled"


def user(uid):
    return SimpleNamespace(id=uid, full_name=f"User {uid}", username=f"u{uid}")


def call(middleware, uid, bot, kind=ChatType.PRIVATE):
    probe = Probe()
    data = {"event_from_user": user(uid), "event_chat": SimpleNamespace(type=kind), "bot": bot}
    result = run(middleware(probe, SimpleNamespace(update_id=1, message=object()), data))
    return result, probe


def test_stranger_files_request_admin_is_notified_and_nothing_runs():
    with tempfile.TemporaryDirectory() as tmp:
        access = make_access(tmp)
        middleware = AdminOnlyMiddleware(ADMIN, access)
        bot = Bot()
        result, probe = call(middleware, 77, bot)
        assert result is None and not probe.guests
        assert access.get(77).status == PENDING
        assert [chat for chat, _ in bot.sent] == [ADMIN, 77]  # админу — заявка, человеку — «ждите»
        # повторные сообщения ожидающего: заявка одна, админа не донимают
        call(middleware, 77, bot)
        assert sum(1 for chat, _ in bot.sent if chat == ADMIN) == 1


def test_service_events_do_not_create_requests():
    with tempfile.TemporaryDirectory() as tmp:
        access = make_access(tmp)
        middleware = AdminOnlyMiddleware(ADMIN, access)
        data = {"event_from_user": user(77), "event_chat": SimpleNamespace(type=ChatType.PRIVATE), "bot": Bot()}
        assert run(middleware(Probe(), SimpleNamespace(update_id=1, message=None, callback_query=None), data)) is None
        assert access.get(77) is None and not data["bot"].sent


def test_approved_guest_passes_with_person_blocked_and_pending_do_not():
    with tempfile.TemporaryDirectory() as tmp:
        access = make_access(tmp)
        middleware = AdminOnlyMiddleware(ADMIN, access)
        bot = Bot()
        call(middleware, 77, bot)
        run(access.set_status(77, APPROVED))
        result, probe = call(middleware, 77, bot)
        assert result == "handled" and probe.guests[0].id == 77
        run(access.set_status(77, BLOCKED))
        sent = len(bot.sent)
        assert call(middleware, 77, bot)[0] is None
        assert len(bot.sent) == sent  # заблокированному бот не отвечает


def test_admin_gets_no_guest_and_groups_are_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        access = make_access(tmp)
        middleware = AdminOnlyMiddleware(ADMIN, access)
        bot = Bot()
        result, probe = call(middleware, ADMIN, bot)
        assert result == "handled" and probe.guests == [None]
        assert call(middleware, ADMIN, bot, ChatType.SUPERGROUP)[0] is None
        run(access.register(8, "x", None))
        run(access.set_status(8, APPROVED))
        assert call(middleware, 8, bot, ChatType.SUPERGROUP)[0] is None  # даже принятый — только личка


def test_without_access_service_strangers_are_dropped_as_before():
    bot = Bot()
    result, probe = call(AdminOnlyMiddleware(ADMIN), 77, bot)
    assert result is None and not probe.guests and not bot.sent


def test_gate_failure_is_fail_closed():
    class Broken:
        def get(self, uid):
            raise RuntimeError("boom")

    bot = Bot()
    result, probe = call(AdminOnlyMiddleware(ADMIN, Broken()), 77, bot)
    assert result is None and not probe.guests


# ------------------------------------------------------------------ данные гостей
def test_notes_and_reminders_are_scoped_per_owner():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "h.db")
            db.open()
            now = int(time.time())
            await db.add_note(now, "админская")
            await db.add_note(now, "гостевая", owner=5)
            assert [n.text for n in await db.list_notes()] == ["админская"]
            guest_notes = await db.list_notes(owner=5)
            assert [n.text for n in guest_notes] == ["гостевая"]
            assert not await db.delete_note(guest_notes[0].id)  # чужую заметку не удалить
            assert await db.delete_note(guest_notes[0].id, owner=5)
            await db.add_reminder(now + 60, "мне")
            await db.add_reminder(now + 60, "гостю", "", 5)
            assert [r.text for r in await db.pending_reminders()] == ["мне"]
            theirs = await db.pending_reminders(owner=5)
            assert [r.text for r in theirs] == ["гостю"] and theirs[0].owner == 5
            assert await db.count_reminders(5) == 1 and await db.count_notes(5) == 0
            assert not await db.delete_reminder(theirs[0].id)
            assert await db.delete_reminder(theirs[0].id, owner=5)
            db.close()

    run(scenario())


def test_personal_prefs_are_cleaned_and_persist():
    from homebot.services.access import MAX_GUEST_FEEDS, clean_prefs

    assert clean_prefs(None) == {} and clean_prefs({"nick": "\n", "count": 4, "presets": "x", "evil": 1}) == {}
    cleaned = clean_prefs({
        "nick": "Вася\n<b>" * 10, "count": 8, "presets": ["tech", "zzz", "tech"],
        "feeds": ["https://a.test/x", "javascript:alert(1)", 5, "https://a.test/x", *[f"https://b{i}.test/" for i in range(20)]],
    })  # fmt: skip
    assert len(cleaned["nick"]) <= 32 and "\n" not in cleaned["nick"] and cleaned["count"] == 8
    assert cleaned["presets"] == ["tech"] and len(cleaned["feeds"]) == MAX_GUEST_FEEDS and cleaned["feeds"][0] == "https://a.test/x"
    with tempfile.TemporaryDirectory() as tmp:
        access = make_access(tmp)
        run(access.register(5, "Вася", None))
        run(access.set_status(5, APPROVED))
        person = run(access.set_prefs(5, nick="Вася-2", presets=["ua"]))
        assert person.prefs == {"nick": "Вася-2", "presets": ["ua"]}
        person = run(access.set_prefs(5, nick=None, count=3))  # None — сбросить поле
        assert person.prefs == {"presets": ["ua"], "count": 3}
        run(access.toggle(5, "notes"))  # смена прав не стирает личные настройки
        assert access.get(5).prefs == {"presets": ["ua"], "count": 3}
        reloaded = make_access(tmp)
        assert reloaded.get(5).prefs == {"presets": ["ua"], "count": 3}
        assert run(access.set_prefs(404, nick="x")) is None
