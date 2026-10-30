"""Настройка логирования: всё уходит в stdout, дальше его забирает journald."""

from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    # Время добавляет сам journald, поэтому в формате его нет.
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    # Шумные библиотеки: подробности только в режиме DEBUG.
    noisy = logging.DEBUG if level == "DEBUG" else logging.WARNING
    logging.getLogger("aiohttp").setLevel(noisy)
    logging.getLogger("aiogram.event").setLevel(noisy)
