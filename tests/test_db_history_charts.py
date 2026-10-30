import asyncio
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from homebot.services.charts import NoDataError, downsample, render_chart
from homebot.services.db import Database, MetricRow
from homebot.services.history import load_period, summarize

KYIV = ZoneInfo("Europe/Kyiv")


def _db(tmp: str) -> Database:
    db = Database(Path(tmp) / "nested" / "homebot.db")
    db.open()
    return db


def test_metrics_roundtrip_period_and_prune():
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _db(tmp)
            now = 1_800_000_000
            for minutes_ago in (60 * 30, 60 * 5, 10, 1):
                ts = now - minutes_ago * 60
                await db.add_metric(MetricRow(ts, 10.0 + minutes_ago % 7, 50.0, 0.0, 45.0, 30.0))
            await db.add_metric(MetricRow(now - 60, 99.0, 51.0, 0.0, None, None))  # перезапись той же секунды

            day = await load_period(db, "24h", now=now)
            assert [r.ts for r in day] == [now - 300 * 60, now - 600, now - 60]
            assert day[-1].cpu == 99.0 and day[-1].temp is None
            week = await load_period(db, "7d", now=now)
            assert len(week) == 4
            assert len(await load_period(db, "unknown", now=now)) == 3  # неизвестный период -> 24 ч

            assert await db.prune_metrics(now - 24 * 3600) == 1
            assert len(await db.metrics_since(0)) == 3
            db.close()

    asyncio.run(scenario())


def test_reminders_and_notes_crud():
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _db(tmp)
            first = await db.add_reminder(100, "раньше")
            second = await db.add_reminder(200, "позже")
            assert [r.text for r in await db.pending_reminders()] == ["раньше", "позже"]
            assert [r.id for r in await db.due_reminders(150)] == [first]
            await db.mark_reminder_done(first)
            assert await db.due_reminders(150) == []
            assert await db.delete_reminder(second) is True
            assert await db.delete_reminder(second) is False
            assert await db.pending_reminders() == []

            a = await db.add_note(1, "первая")
            await db.add_note(2, "вторая")
            assert [n.text for n in await db.list_notes()] == ["вторая", "первая"]  # новые сверху
            assert await db.delete_note(a) is True
            assert [n.text for n in await db.list_notes()] == ["вторая"]
            db.close()

    asyncio.run(scenario())


def test_data_survives_reopen():
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _db(tmp)
            await db.add_note(1, "персистентно")
            db.close()
            again = _db(tmp)
            assert [n.text for n in await again.list_notes()] == ["персистентно"]
            again.close()

    asyncio.run(scenario())


def test_summarize():
    assert summarize([]) is None
    rows = [MetricRow(1, 10, 40, 0, 50, 20), MetricRow(2, 30, 60, 0, None, 21), MetricRow(3, 20, 50, 0, 70, None)]
    stats = summarize(rows)
    assert stats.samples == 3 and stats.cpu_avg == 20 and stats.cpu_max == 30
    assert stats.ram_max == 60 and stats.temp_max == 70 and stats.temp_avg == 60 and stats.disk_last == 21


def test_downsample_keeps_shape():
    rows = [MetricRow(i, float(i % 100), 50.0, 0.0, None, 10.0) for i in range(5000)]
    small = downsample(rows, max_points=500)
    assert len(small) <= 500
    assert small[0].ts < small[-1].ts
    assert all(r.temp is None for r in small) and all(r.disk == 10.0 for r in small)
    assert downsample(rows[:10]) == rows[:10]


def test_render_chart_png_with_gaps_and_optional_panels():
    now = int(time.time())
    rows = [MetricRow(now - 7200 + i * 60, 20 + i % 30, 55.0, 1.0, 48.0 + i % 5, 31.0) for i in range(60)]
    rows += [MetricRow(now - 1800 + i * 60, 40.0, 60.0, 1.0, 50.0, 31.0) for i in range(20)]  # разрыв ~1 час
    png = render_chart(rows, tz=KYIV, title="тест · 24 часа")
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 10_000

    no_sensors = [MetricRow(now - i * 60, 5.0, 30.0, 0.0, None, None) for i in range(30, 0, -1)]
    assert render_chart(no_sensors, tz=KYIV, title="без датчиков", long_period=True)[:4] == b"\x89PNG"
    try:
        render_chart([], tz=KYIV, title="пусто")
    except NoDataError:
        pass
    else:
        raise AssertionError("NoDataError expected")
