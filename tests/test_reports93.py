"""9.3: часовые итоги, отчёты за неделю/месяц/год, еженедельная отправка, проверка .env."""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from homebot.jobs.weekly_report import due
from homebot.services import envcheck, reports
from homebot.services.db import Database, MetricRow

NOW = 1_800_000_000 - 1_800_000_000 % 3600


def _db(tmp_path):
    db = Database(tmp_path / "h.db")
    db.open()
    return db


def test_rollup_and_report(tmp_path):
    db = _db(tmp_path)

    async def go():
        # 10 дней по точке в час: CPU 10% в прошлой неделе, 30% в этой; диск растёт
        for i in range(14 * 24):
            ts = NOW - (14 * 24 - i) * 3600
            cpu = 30.0 if ts >= NOW - 7 * 86400 else 10.0
            await db.add_metric(MetricRow(ts=ts, cpu=cpu, ram=50, swap=0, temp=40 + (i % 5), disk=50 + i / 100))
            await db.add_metric(MetricRow(ts=ts + 60, cpu=cpu + 20, ram=60, swap=0, temp=None, disk=None))
        rolled = await db.rollup_hours(NOW)
        assert rolled == 14 * 24
        hours = await db.hourly_since(0)
        assert hours[0].cpu_max == 30 and hours[0].samples == 2 and hours[0].temp_max is not None
        # подробные строки можно удалить — отчёт останется
        await db.prune_metrics(NOW + 1)
        return await reports.make(db, "week", now=NOW)

    rep = asyncio.run(go())
    assert rep is not None
    assert rep.cpu_avg == 40 and rep.prev_cpu_avg == 20  # среднее (30+50)/2 и (10+30)/2
    assert rep.cpu_max == 50 and rep.ram_avg == 55
    assert rep.disk_end > rep.disk_start
    assert rep.coverage == 1.0 and len(rep.points) >= 7
    text = reports.render(rep, "week", ZoneInfo("UTC"))
    assert "40%" in text and "+20 п.п." in text and "Диск /" in text
    assert reports.as_json(rep)["period"] == "week"


def test_empty_report(tmp_path):
    db = _db(tmp_path)
    assert asyncio.run(reports.make(db, "year", now=NOW)) is None
    assert "Истории пока нет" in reports.render(None, "year")


def test_weekly_due():
    tz = ZoneInfo("UTC")
    monday = datetime(2026, 11, 2, 10, 5, tzinfo=tz)
    assert due(monday, "") == "2026-W45"
    assert due(monday, "2026-W45") is None
    assert due(datetime(2026, 11, 2, 9, 0, tzinfo=tz), "") is None
    assert due(datetime(2026, 11, 3, 12, 0, tzinfo=tz), "") is None


def test_env_unknown_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text("BOT_TOKEN=1\n# OLD_KEY=1\nOLD_THING=2\nCATALOG_ROOT=/x\nexport TIMEZONE=UTC\nOLD_THING=3\n")
    assert envcheck.unknown_keys(env) == ["OLD_THING"]
    assert envcheck.unknown_keys(tmp_path / "none") == []
