"""Middleware Telegram-сессии: копия сообщений фоновых задач администратору — в 🔔 центр уведомлений."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import SendDocument, SendMessage, SendPhoto, TelegramMethod

from homebot.services.inbox import Inbox
from homebot.services.quiet import Importance, current_importance

log = logging.getLogger(__name__)


class InboxMiddleware(BaseRequestMiddleware):
    def __init__(self, inbox: Inbox, admin_id: int) -> None:
        self._inbox = inbox
        self._admin_id = admin_id

    async def __call__(self, make_request: NextRequestMiddlewareType[Any], bot: Bot, method: TelegramMethod[Any]) -> Any:
        result = await make_request(bot, method)
        level = current_importance()
        if level != Importance.INTERACTIVE and isinstance(method, SendMessage | SendPhoto | SendDocument):
            if str(getattr(method, "chat_id", "")) == str(self._admin_id):
                text = getattr(method, "text", None) or getattr(method, "caption", None) or ""
                try:
                    self._inbox.add(str(text), critical=level == Importance.CRITICAL)
                except Exception:  # никогда не мешаем отправке
                    log.exception("Inbox add failed")
        return result
