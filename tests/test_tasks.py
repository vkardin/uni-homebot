import asyncio
import tempfile
from pathlib import Path

from homebot.services.state import StateStore
from homebot.services.tasks import TaskManager


class Counter:
    def __init__(self) -> None:
        self.started = 0
        self.cancelled = 0

    async def forever(self) -> None:
        self.started += 1
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


def _manager(tmp: str) -> tuple[TaskManager, StateStore]:
    store = StateStore(Path(tmp) / "state.json")
    store.load()
    return TaskManager(store), store


def test_toggle_starts_and_stops_and_persists():
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager, store = _manager(tmp)
            counter = Counter()
            manager.register("job", "Job", "desc", counter.forever)
            assert not manager.is_running("job")

            assert await manager.toggle("job") is True
            await asyncio.sleep(0)
            assert manager.is_running("job") and counter.started == 1
            assert store.get("task.job.enabled") is True

            assert await manager.toggle("job") is False
            assert not manager.is_running("job") and counter.cancelled == 1
            assert store.get("task.job.enabled") is False

    asyncio.run(scenario())


def test_start_enabled_uses_saved_choice_over_default():
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager, store = _manager(tmp)
            on_by_default, off_by_default, saved_off = Counter(), Counter(), Counter()
            manager.register("a", "A", "", on_by_default.forever, default_enabled=True)
            manager.register("b", "B", "", off_by_default.forever, default_enabled=False)
            manager.register("c", "C", "", saved_off.forever, default_enabled=True)
            await store.set("task.c.enabled", False)  # пользователь выключил кнопкой раньше

            await manager.start_enabled()
            await asyncio.sleep(0)
            assert (on_by_default.started, off_by_default.started, saved_off.started) == (1, 0, 0)

            await manager.shutdown()
            assert on_by_default.cancelled == 1
            assert store.get("task.a.enabled") is None  # shutdown не перезаписывает настройки

    asyncio.run(scenario())


def test_crashing_task_is_contained():
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager, _ = _manager(tmp)

            async def boom() -> None:
                raise RuntimeError("boom")

            manager.register("boom", "Boom", "", boom)
            manager._backoff = 0.0001  # перезапуски после падений — без долгих пауз
            await manager.toggle("boom")
            await asyncio.sleep(0.2)
            assert not manager.is_running("boom")  # упала (после перезапусков), но бот и менеджер живы
            assert manager.broken() == [("Boom", "RuntimeError: boom")]
            assert await manager.toggle("boom") is True  # и её можно запустить снова

    asyncio.run(scenario())


def test_unknown_and_duplicate_names():
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager, _ = _manager(tmp)
            manager.register("x", "X", "", Counter().forever)
            try:
                manager.register("x", "X", "", Counter().forever)
            except ValueError:
                pass
            else:
                raise AssertionError("duplicate registration must fail")
            try:
                await manager.toggle("nope")
            except KeyError:
                pass
            else:
                raise AssertionError("unknown task must raise KeyError")

    asyncio.run(scenario())
