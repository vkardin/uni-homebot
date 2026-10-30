"""Важность уведомлений для 🌙 ночного режима.

Фоновая задача один раз объявляет, насколько важны её сообщения (`set_importance` в начале `run`),
а middleware Telegram-сессии (`utils/quiet_middleware.py`) ночью отправляет обычные сообщения беззвучно.

* INTERACTIVE — ответы на ваши действия и напоминания, которые вы сами поставили: всегда со звуком;
* NORMAL — новости, игры, торренты, кино, отчёты: ночью без звука;
* CRITICAL — ошибки дисков, упавшие контейнеры, вход по SSH, перегрев: всегда со звуком.
"""

from __future__ import annotations

from contextvars import ContextVar
from enum import IntEnum

from homebot.services.prefs import NightPrefs


class Importance(IntEnum):
    INTERACTIVE = 0
    NORMAL = 1
    CRITICAL = 2


_level: ContextVar[Importance] = ContextVar("notify_importance", default=Importance.INTERACTIVE)


def set_importance(level: Importance) -> None:
    _level.set(level)


def current_importance() -> Importance:
    return _level.get()


def should_silence(level: Importance, night: NightPrefs, hour: int) -> bool:
    return level == Importance.NORMAL and night.is_quiet(hour)
