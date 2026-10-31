"""Менеджер фоновых задач: запуск/остановка по кнопке, состояние переживает перезапуск бота."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homebot.services.state import StateStore

log = logging.getLogger(__name__)

TaskFactory = Callable[[], Awaitable[None]]

# 🩹 Самовосстановление: упавшая задача перезапускается с паузой 1, 2, 4… мин (не больше 15);
# после MAX_CRASHES падений подряд она останавливается до кнопки «Починить» в /doctor.
MAX_CRASHES = 5
STABLE_SEC = 3600  # проработала час без падений — счётчик сбрасывается


@dataclass(frozen=True, slots=True)
class TaskInfo:
    """Снимок задачи для отображения в интерфейсе."""

    name: str
    title: str
    description: str
    running: bool


@dataclass(slots=True)
class _ManagedTask:
    name: str
    title: str
    description: str
    factory: TaskFactory
    default_enabled: bool
    hidden: bool = False
    handle: asyncio.Task[None] | None = None
    crashes: int = 0
    last_error: str = ""
    gave_up: bool = False

    @property
    def running(self) -> bool:
        return self.handle is not None and not self.handle.done()


class TaskManager:
    def __init__(self, store: StateStore, *, backoff: float = 60.0) -> None:
        self._store = store
        self._tasks: dict[str, _ManagedTask] = {}
        self._backoff = backoff

    def register(
        self,
        name: str,
        title: str,
        description: str,
        factory: TaskFactory,
        *,
        default_enabled: bool = False,
        hidden: bool = False,
    ) -> None:
        """`hidden=True` — служебная задача: стартует всегда и не показывается в списке кнопок."""
        if name in self._tasks:
            raise ValueError(f"task {name!r} is already registered")
        self._tasks[name] = _ManagedTask(name, title, description, factory, default_enabled, hidden)

    def unregister(self, name: str) -> None:
        """Убрать ещё не запущенную задачу (модуль выключен); неизвестное имя — без ошибки."""
        self._tasks.pop(name, None)

    def items(self) -> list[TaskInfo]:
        return [TaskInfo(t.name, t.title, t.description, t.running) for t in self._tasks.values() if not t.hidden]

    def is_running(self, name: str) -> bool:
        return self._get(name).running

    async def start_enabled(self) -> None:
        """Запускает задачи, включённые пользователем (или включённые по умолчанию в конфиге)."""
        for task in self._tasks.values():
            if task.hidden or self._store.get(self._key(task.name), task.default_enabled):
                self._spawn(task)

    async def toggle(self, name: str) -> bool:
        """Переключает задачу, запоминает выбор. Возвращает новое состояние (True = работает)."""
        task = self._get(name)
        if task.running:
            await self._cancel(task)
            await self._store.set(self._key(name), False)
            return False
        self._spawn(task)
        await self._store.set(self._key(name), True)
        return True

    def broken(self) -> list[tuple[str, str]]:
        """(название, последняя ошибка) задач, остановленных после череды падений."""
        return [(t.title or t.name, t.last_error) for t in self._tasks.values() if t.gave_up]

    def repair(self) -> int:
        """🔧 Перезапустить остановленные после падений задачи; сколько перезапущено."""
        count = 0
        for task in self._tasks.values():
            if task.gave_up:
                self._spawn(task)
                count += 1
        return count

    async def shutdown(self) -> None:
        """Останавливает все задачи (не меняя сохранённых настроек)."""
        for task in self._tasks.values():
            await self._cancel(task)

    # ------------------------------------------------------------ внутреннее
    def _get(self, name: str) -> _ManagedTask:
        try:
            return self._tasks[name]
        except KeyError:
            raise KeyError(f"unknown task: {name}") from None

    @staticmethod
    def _key(name: str) -> str:
        return f"task.{name}.enabled"

    def _spawn(self, task: _ManagedTask) -> None:
        if task.running:
            return
        task.gave_up, task.crashes = False, 0
        task.handle = asyncio.create_task(self._guard(task), name=f"job:{task.name}")
        log.info("Background task started: %s", task.name)

    @staticmethod
    async def _cancel(task: _ManagedTask) -> None:
        handle = task.handle
        if handle is None or handle.done():
            return
        handle.cancel()
        await asyncio.gather(handle, return_exceptions=True)
        log.info("Background task stopped: %s", task.name)

    async def _guard(self, task: _ManagedTask) -> None:
        while True:
            started = time.monotonic()
            try:
                await task.factory()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Background task %s crashed", task.name)
                if time.monotonic() - started > STABLE_SEC:
                    task.crashes = 0
                task.crashes += 1
                task.last_error = f"{type(exc).__name__}: {exc}"[:200]
            if task.crashes >= MAX_CRASHES:
                task.gave_up = True
                log.error("Background task %s stopped after %d crashes", task.name, task.crashes)
                return
            await asyncio.sleep(min(self._backoff * 2 ** (task.crashes - 1), 15 * self._backoff))
