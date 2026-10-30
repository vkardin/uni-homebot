"""Очередь к локальной модели с приоритетами.

Модель на CPU одна и медленная (≈4 токена/с), а желающих много: ваш чат, разбор упавшего контейнера,
пересказ новостей, перевод заголовков. Порядок обслуживания:

* USER — то, что вы ждёте прямо сейчас (чат, резюме, «объясни журнал», подбор фильма);
* ALERT — фоновый разбор проблем (Docker);
* BACKGROUND — новости, переводы, всё, что может подождать.

Если вы пишете, пока модель занята фоновой работой, фоновый запрос прерывается (Ollama перестаёт
генерировать, как только клиент отключился) и получает `OllamaPreempted` — у новостей на этот случай
есть запасной вариант (оригинальный заголовок). Свой приоритет задача объявляет один раз в начале
работы: `set_priority(Priority.BACKGROUND)` — значение живёт в контексте задачи asyncio и наследуется
задачами, которые она создаёт.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import IntEnum


class Priority(IntEnum):
    USER = 0
    ALERT = 1
    BACKGROUND = 2


_current: ContextVar[Priority] = ContextVar("llm_priority", default=Priority.USER)

PRIORITY_LABELS = {Priority.USER: "ваш запрос", Priority.ALERT: "разбор проблемы", Priority.BACKGROUND: "фоновая работа"}


def set_priority(priority: Priority) -> None:
    """Вызывается в начале фоновой задачи: все её обращения к модели получат этот приоритет."""
    _current.set(priority)


def current_priority() -> Priority:
    return _current.get()


@dataclass(eq=False, slots=True)
class Slot:
    """Право на генерацию. `task` — выполняемый запрос (его можно прервать ради USER)."""

    priority: Priority
    task: asyncio.Task | None = None
    preempted: bool = False
    _granted: asyncio.Future | None = field(default=None, repr=False)


class PriorityGate:
    """Как asyncio.Lock, но очередь упорядочена по приоритету, а внутри — по времени прихода."""

    def __init__(self) -> None:
        self._holder: Slot | None = None
        self._queue: list[tuple[int, int, Slot]] = []
        self._order = itertools.count()

    @property
    def holder(self) -> Slot | None:
        return self._holder

    @property
    def waiting(self) -> int:
        return sum(1 for *_, slot in self._queue if slot._granted is not None and not slot._granted.done())

    async def acquire(self, priority: Priority | None = None) -> Slot:
        slot = Slot(current_priority() if priority is None else priority)
        if self._holder is None and not self.waiting:
            self._holder = slot
            return slot
        self._maybe_preempt(slot.priority)
        slot._granted = asyncio.get_running_loop().create_future()
        heapq.heappush(self._queue, (int(slot.priority), next(self._order), slot))
        try:
            await slot._granted
        except asyncio.CancelledError:
            if slot._granted.done() and not slot._granted.cancelled():
                self.release(slot)  # очередь дошла, но нас отменили — передаём право следующему
            raise
        return slot

    def release(self, slot: Slot) -> None:
        if self._holder is not slot:
            return
        self._holder = None
        while self._queue:
            *_, nxt = heapq.heappop(self._queue)
            if nxt._granted is None or nxt._granted.done():
                continue  # ожидающего отменили, пока он стоял в очереди
            self._holder = nxt
            nxt._granted.set_result(None)
            return

    def _maybe_preempt(self, priority: Priority) -> None:
        holder = self._holder
        if (
            priority == Priority.USER
            and holder is not None
            and holder.priority == Priority.BACKGROUND
            and holder.task is not None
            and not holder.task.done()
        ):
            holder.preempted = True
            holder.task.cancel()
