import asyncio
import json
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from aiogram.exceptions import TelegramAPIError

from homebot.jobs.maintenance import MaintenanceJob
from homebot.jobs.reminders import RemindersJob
from homebot.jobs.sampler import to_row
from homebot.services.db import Database
from homebot.services.smart import SmartService
from homebot.services.state import StateStore
from homebot.services.system import DiskInfo, SystemSnapshot, TempReading
from homebot.services.tasks import TaskManager
from homebot.services.updates import UpdatesInfo


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail = False

    async def send_message(self, chat_id, text):
        if self.fail:
            raise TelegramAPIError(method=None, message="offline")  # type: ignore[arg-type]
        self.sent.append(text)


def test_sampler_row_uses_root_disk_and_optional_temp():
    snap = SystemSnapshot(
        taken_at=datetime.now(UTC), cpu_percent=12.5, cpu_threads=4, load_avg=None, ram_total=1, ram_used=1,
        ram_percent=40.0, swap_total=0, swap_used=0, swap_percent=0.0,
        disks=(DiskInfo("/boot", 1, 1, 90.0), DiskInfo("/", 1, 1, 33.0)), temperature=None, uptime_sec=1,
    )  # fmt: skip
    row = to_row(snap, 123)
    assert (row.ts, row.cpu, row.ram, row.disk, row.temp) == (123, 12.5, 40.0, 33.0, None)
    with_temp = to_row(replace(snap, temperature=TempReading("c", "p", 50.0, None), disks=()), 1)
    assert with_temp.temp == 50.0 and with_temp.disk is None


def test_reminders_job_delivers_once_and_retries_on_failure():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "db.sqlite")
            db.open()
            now = int(time.time())
            await db.add_reminder(now - 5, "оплатить <интернет>")
            await db.add_reminder(now + 3600, "потом")
            bot = FakeBot()
            job = RemindersJob(bot=bot, admin_id=1, db=db)  # type: ignore[arg-type]

            bot.fail = True
            assert await job.tick() == 0  # сеть упала — напоминание не потеряно
            bot.fail = False
            assert await job.tick() == 1
            assert await job.tick() == 0  # повторно не шлём
            assert bot.sent == ["⏰ <b>Напоминание</b>\nоплатить &lt;интернет&gt;"]
            assert [r.text for r in await db.pending_reminders()] == ["потом"]
            db.close()

    asyncio.run(scenario())


class FakeUpdates:
    def __init__(self, info: UpdatesInfo) -> None:
        self.info = info

    async def check(self) -> UpdatesInfo:
        return self.info


def test_maintenance_notifies_only_on_changes():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            smart_file = Path(tmp) / "smart.json"
            bot = FakeBot()
            updates = FakeUpdates(UpdatesInfo(total=5, security=0))
            job = MaintenanceJob(bot=bot, admin_id=1, store=store, smart=SmartService(smart_file), updates=updates)  # type: ignore[arg-type]

            await job.check_smart()  # файла нет — тишина
            await job.check_updates()  # обновления есть, но не security — тишина
            assert bot.sent == []

            smart_file.write_text(json.dumps({"updated": int(time.time()), "disks": [{"device": "/dev/sda", "passed": False}]}))
            await job.check_smart()
            await job.check_smart()
            assert len(bot.sent) == 1 and "/dev/sda" in bot.sent[0]

            updates.info = UpdatesInfo(total=6, security=2, reboot_required=True)
            await job.check_updates()
            await job.check_updates()
            assert len(bot.sent) == 2 and "безопасности: <b>2</b>" in bot.sent[1] and "перезагрузка" in bot.sent[1]

            smart_file.write_text(json.dumps({"updated": int(time.time()), "disks": [{"device": "/dev/sda", "passed": True}]}))
            await job.check_smart()  # починилось — сброс без сообщения
            assert len(bot.sent) == 2 and store.get("maint.smart_signature") == []

    asyncio.run(scenario())


def test_hidden_tasks_always_start_and_are_not_listed():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            manager = TaskManager(store)
            started = []

            async def job(name):
                started.append(name)
                await asyncio.sleep(3600)

            manager.register("sampler", "S", "", lambda: job("sampler"), hidden=True)
            manager.register("alerts", "A", "", lambda: job("alerts"), default_enabled=False)
            await store.set("task.sampler.enabled", False)  # даже «выключенная» служебная задача стартует
            await manager.start_enabled()
            await asyncio.sleep(0)
            assert started == ["sampler"]
            assert [t.name for t in manager.items()] == ["alerts"]
            await manager.shutdown()

    asyncio.run(scenario())
