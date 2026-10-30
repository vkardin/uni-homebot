"""Реальный psutil: проверяем, что сбор метрик работает на этой машине и не падает без датчиков/прав."""

import asyncio
from zoneinfo import ZoneInfo

from homebot.services.system import SystemService
from homebot.ui.texts import render_status, render_top


def test_snapshot_is_sane_and_renders():
    service = SystemService(ZoneInfo("Europe/Kyiv"))
    snap = asyncio.run(service.snapshot())

    assert 0 <= snap.cpu_percent <= 100 and snap.cpu_threads >= 1
    assert 0 <= snap.ram_percent <= 100
    assert 0 < snap.ram_used <= snap.ram_total
    assert snap.uptime_sec > 0
    assert snap.taken_at.tzinfo is not None
    for disk in snap.disks:
        assert disk.total > 0 and 0 <= disk.percent <= 100
        assert not disk.mountpoint.startswith(("/snap", "/proc", "/sys", "/dev"))  # служебные ФС отфильтрованы
    assert len({d.mountpoint for d in snap.disks}) == len(snap.disks)

    text = render_status(snap)
    assert "CPU" in text and "RAM" in text and "Аптайм" in text
    assert len(text) < 4096


def test_top_processes_are_sorted_and_limited():
    processes = asyncio.run(SystemService().top_processes(limit=3))
    assert len(processes) <= 3
    assert [p.rss for p in processes] == sorted((p.rss for p in processes), reverse=True)
    assert "Топ процессов" in render_top(processes)
    assert "Нет данных" in render_top([])
