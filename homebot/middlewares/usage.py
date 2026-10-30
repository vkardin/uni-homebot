from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject

from homebot.services import usage

log = logging.getLogger(__name__)


class UsageMiddleware(BaseMiddleware):
    """Считает нажатия разделов меню. Ошибка счётчика никогда не мешает обработке."""

    async def __call__(
        self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        store = data.get("store")
        if store is not None and data.get("guest") is None and isinstance(event, CallbackQuery):  # только админ
            section = usage.section_of(event.data)
            if section:
                try:
                    await usage.count(store, section)
                except Exception:
                    log.debug("usage counter failed", exc_info=True)
        return await handler(event, data)
