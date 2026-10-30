from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from homebot.services.db import Database
from homebot.services.reminders import describe_rule, next_due, parse_recurring

TZ = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)  # среда


def test_parse_weekly_daily_workdays_monthly_every():
    weekly = parse_recurring("каждый понедельник 9:00 планёрка", NOW)
    assert (
        weekly
        and weekly.rule == "weekly:0:09:00"
        and weekly.text == "планёрка"
        and weekly.due == datetime(2026, 10, 19, 9, 0, tzinfo=TZ)
    )
    assert parse_recurring("каждую пятницу 18:30 пиво", NOW).rule == "weekly:4:18:30"
    daily = parse_recurring("каждый день 8:30 таблетки", NOW)
    assert daily.rule == "daily:08:30" and daily.due == datetime(2026, 10, 15, 8, 30, tzinfo=TZ)
    assert parse_recurring("ежедневно в 7:00 зарядка", NOW).rule == "daily:07:00"
    assert parse_recurring("по будням 18:00 отчёт", NOW).rule == "workdays:18:00"
    monthly = parse_recurring("каждый месяц 15 числа 10:00 оплатить", NOW)
    assert monthly.rule == "monthly:15:10:00" and monthly.due == datetime(2026, 10, 15, 10, 0, tzinfo=TZ)
    every = parse_recurring("каждые 2 часа размяться", NOW)
    assert every.rule == "every:7200" and every.due == datetime(2026, 10, 14, 14, 0, tzinfo=TZ)


def test_parse_recurring_rejects_garbage():
    assert parse_recurring("завтра 9:00 обычное", NOW) is None
    assert parse_recurring("каждый день 25:00 текст", NOW) is None
    assert parse_recurring("каждый день 9:00", NOW) is None  # нет текста
    assert parse_recurring("каждые 1 мин спам", NOW) is None  # слишком часто
    assert parse_recurring("каждый месяц 40 числа 9:00 x", NOW) is None


def test_next_due_rules():
    assert next_due("daily:08:00", NOW) == datetime(2026, 10, 15, 8, 0, tzinfo=TZ)
    assert next_due("daily:13:00", NOW) == datetime(2026, 10, 14, 13, 0, tzinfo=TZ)
    friday = datetime(2026, 10, 16, 17, 0, tzinfo=TZ)
    assert next_due("workdays:09:00", friday) == datetime(2026, 10, 19, 9, 0, tzinfo=TZ)  # пятница → понедельник
    assert next_due("weekly:2:12:00", NOW) == datetime(2026, 10, 21, 12, 0, tzinfo=TZ)  # ровно сейчас → через неделю
    jan = datetime(2026, 1, 30, 12, 0, tzinfo=TZ)
    assert next_due("monthly:31:09:00", jan) == datetime(2026, 1, 31, 9, 0, tzinfo=TZ)
    assert next_due("monthly:31:09:00", datetime(2026, 2, 1, 0, 0, tzinfo=TZ)) == datetime(2026, 2, 28, 9, 0, tzinfo=TZ)
    anchor = datetime(2026, 10, 14, 6, 0, tzinfo=TZ)
    assert next_due("every:7200", NOW, anchor) == datetime(2026, 10, 14, 14, 0, tzinfo=TZ)  # пропущенные не повторяем пачкой
    assert describe_rule("weekly:0:09:00") == "каждый пн в 09:00" and describe_rule("every:7200") == "каждые 2 ч"
    assert describe_rule("weekly:x") == "повторяется"


def test_db_repeat_roundtrip_and_migration():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            import sqlite3

            old = sqlite3.connect(path)
            old.execute(
                "CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, due_ts INTEGER NOT NULL, text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0)"
            )
            old.execute("INSERT INTO reminders (due_ts, text) VALUES (5, 'старое')")
            old.commit()
            old.close()
            db = Database(path)
            db.open()
            rid = await db.add_reminder(10, "новое", "daily:08:00")
            items = await db.pending_reminders()
            assert [(i.text, i.repeat) for i in items] == [("старое", ""), ("новое", "daily:08:00")]
            await db.reschedule_reminder(rid, 99)
            assert (await db.pending_reminders())[1].due_ts == 99
            db.close()

    asyncio.run(scenario())
