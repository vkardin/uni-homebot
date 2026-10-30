"""Глобальный обработчик ошибок: логирует traceback и аккуратно сообщает администратору."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent

log = logging.getLogger(__name__)
router = Router(name="errors")

_USER_MESSAGE = "⚠️ Ой, у меня что-то сломалось. Подробности в журнале: <code>journalctl -u homebot -e</code>"


@router.error()
async def on_error(event: ErrorEvent) -> bool:
    update = event.update
    log.error("Unhandled error in update %s", update.update_id, exc_info=event.exception)

    try:
        if update.callback_query is not None:
            await update.callback_query.answer("⚠️ Ой, что-то сломалось — см. журнал", show_alert=True)
        elif update.message is not None:
            await update.message.answer(_USER_MESSAGE)
    except TelegramAPIError as exc:  # например, на callback уже ответили или пропала сеть
        log.debug("Cannot notify about the error: %s", exc)

    return True  # ошибка обработана — не пробрасываем дальше и не роняем polling
