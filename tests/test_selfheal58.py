"""5.8: упавшие фоновые задачи перезапускаются сами, после череды падений — стоп и «Починить»."""

from __future__ import annotations

import asyncio

from homebot.services import tasks as tasks_mod
from homebot.services.state import StateStore
from homebot.services.tasks import TaskManager


def _manager(tmp_path) -> TaskManager:
    store = StateStore(tmp_path / "state.json")
    store.load()
    return TaskManager(store, backoff=0.001)


def test_restarts_then_recovers(tmp_path):
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("boom")

    async def run():
        m = _manager(tmp_path)
        m.register("flaky", "Нестабильная", "", flaky, hidden=True)
        await m.start_enabled()
        await asyncio.sleep(0.2)
        return m

    m = asyncio.run(run())
    assert len(calls) == 3
    assert m.broken() == []


def test_gives_up_and_repair(tmp_path):
    calls = []

    async def broken():
        calls.append(1)
        raise ValueError("нет ключа")

    async def run():
        m = _manager(tmp_path)
        m.register("bad", "Сломанная", "", broken, hidden=True)
        await m.start_enabled()
        await asyncio.sleep(0.3)
        assert len(calls) == tasks_mod.MAX_CRASHES
        assert m.broken() == [("Сломанная", "ValueError: нет ключа")]
        assert m.repair() == 1
        assert m.broken() == []
        await asyncio.sleep(0.3)
        assert len(calls) == 2 * tasks_mod.MAX_CRASHES
        assert m.repair() == 1
        await m.shutdown()

    asyncio.run(run())
