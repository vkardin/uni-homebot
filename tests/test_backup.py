from __future__ import annotations

import asyncio
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from homebot.jobs.backup import BackupJob
from homebot.services import backup as svc
from homebot.services.db import Database
from homebot.services.state import StateStore

TZ = ZoneInfo("Europe/Kyiv")


def _env(tmp: str):
    state_dir = Path(tmp)
    db = Database(state_dir / "homebot.db")
    db.open()
    store = StateStore(state_dir / "state.json")
    store.load()
    return state_dir, db, store


def test_backup_roundtrip_keeps_extra_and_skips_env():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as extra_dir:
            state_dir, db, store = _env(tmp)
            (Path(extra_dir) / "compose.yml").write_text("services: {}")
            (Path(extra_dir) / ".env").write_text(
                "TOKEN=1"
            )  # попадёт, только если указан путь: проверим, что бэкап берёт лишь заданное
            await db.add_note(1, "важная заметка")
            await store.set("k", {"a": 1})
            item = await svc.create_backup(state_dir, db, keep=7, extra=[Path(extra_dir) / "compose.yml"], tz=TZ)
            names = tarfile.open(item.path).getnames()
            assert svc.DB_NAME in names and svc.STATE_NAME in names
            assert any(n.endswith("compose.yml") for n in names) and not any(n.endswith(".env") for n in names)
            await db.delete_note((await db.list_notes())[0].id)
            await store.set("k", {"a": 2})
            done = await svc.restore_backup(item.path, state_dir, db, store)
            assert done == ["база", "состояние"]
            assert [n.text for n in await db.list_notes()] == ["важная заметка"]
            assert store.get("k") == {"a": 1}
            db.close()

    asyncio.run(scenario())


def test_prune_and_list_and_broken_archive():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            state_dir, db, store = _env(tmp)
            folder = svc.backup_dir(state_dir)
            folder.mkdir()
            for day in range(1, 11):
                (folder / f"homebot-202601{day:02d}-040000.tar.gz").write_bytes(b"x")
            (folder / "garbage.txt").write_text("-")
            assert svc.prune(state_dir, 7) == 3
            items = svc.list_backups(state_dir)
            assert len(items) == 7 and items[0].name.startswith("homebot-20260110")
            try:
                await svc.restore_backup(items[0].path, state_dir, db, store)
            except svc.BackupError as exc:
                assert "архив" in str(exc)
            else:
                raise AssertionError("битый архив не должен восстанавливаться")
            db.close()

    asyncio.run(scenario())


def test_job_due_logic_and_failure_is_recorded():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            state_dir, db, store = _env(tmp)

            class Bot:
                def __init__(self):
                    self.sent: list[str] = []

                async def send_message(self, chat_id, text, **kw):
                    self.sent.append(text)

            bot = Bot()
            job = BackupJob(bot=bot, admin_id=1, db=db, store=store, state_dir=state_dir, hour=4, keep=3, extra=[], tz=TZ)  # type: ignore[arg-type]
            assert job.due(datetime(2026, 1, 5, 3, 59, tzinfo=TZ)) is False  # рано
            await job.make()
            assert svc.last_status(store)["ok"] is True
            now = datetime.now(TZ)
            assert job.due(now.replace(hour=23, minute=59)) is False  # сегодня уже делали
            db.close()
            assert await job.make() is None  # база закрыта — ошибка записана и показана, а не упала
            assert svc.last_status(store)["ok"] is False and bot.sent and "Бэкап не получился" in bot.sent[0]

    asyncio.run(scenario())


def test_texts_render():
    from homebot.ui.backup_texts import render_backup_home, size_text

    assert size_text(500) == "1 КБ" and size_text(3 * 1024 * 1024) == "3.0 МБ"
    text = render_backup_home([], {"ok": False, "error": "диск <полон>"}, 4, 7, TZ, 0)
    assert "&lt;полон&gt;" in text and "Копий пока нет" in text


def test_usage_counter():
    from homebot.services import usage

    assert usage.section_of("m:sites") == "sites" and usage.section_of("m:home") is None and usage.section_of("z:ck") is None

    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            store.load()
            for name in ("sites", "sites", "news"):
                await usage.count(store, name)
            assert usage.top(store) == [("sites", 2), ("news", 1)]
            assert "Сайты" in usage.render(usage.top(store)) and "Пока пусто" in usage.render([])

    asyncio.run(scenario())


def test_quick_helpers_and_voice_drafts():
    from homebot.services.quickadd import MAGNET_RE as _MAGNET_RE
    from homebot.services.quickadd import ONLY_URLS_RE as _ONLY_URLS_RE
    from homebot.services.quickadd import magnet_name
    from homebot.services.voice import Drafts

    magnet = "magnet:?xt=urn:btih:ABC123&dn=Film%20Name%202026&tr=udp%3A%2F%2Fx"
    assert _MAGNET_RE.search(f"вот {magnet} держи").group(0).startswith("magnet:?xt=urn:btih:ABC123")
    assert magnet_name(magnet) == "Film Name 2026" and magnet_name("magnet:?xt=urn:btih:1") == "без названия"
    assert _ONLY_URLS_RE.match("https://a.ua/x\nhttps://b.ua/y") and not _ONLY_URLS_RE.match("глянь https://a.ua/x")
    drafts = Drafts()
    refs = [drafts.put(f"t{i}", 5) for i in range(25)]
    assert drafts.get(refs[0]) is None and drafts.get(refs[-1]) == "t24"
    drafts.drop(refs[-1])
    assert drafts.get(refs[-1]) is None


def test_qbit_add_magnet_reports_refusal():
    from homebot.services.apps_http import AppError
    from homebot.services.qbittorrent import QbitService

    class Fake(QbitService):
        def __init__(self, reply):
            self.reply = reply

        async def _call(self, method, path, *, params=None, data=None):
            assert path == "/api/v2/torrents/add" and data == {"urls": "magnet:?xt=urn:btih:1"}
            return self.reply

    asyncio.run(Fake("Ok.").add_magnet("magnet:?xt=urn:btih:1"))
    try:
        asyncio.run(Fake("Fails.").add_magnet("magnet:?xt=urn:btih:1"))
    except AppError as exc:
        assert "не принял" in str(exc)
    else:
        raise AssertionError
