"""Middleware Telegram-сессии: ночью обычные уведомления фоновых задач уходят беззвучно."""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Any

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import (
    SendAnimation,
    SendAudio,
    SendDocument,
    SendMediaGroup,
    SendMessage,
    SendPhoto,
    SendVideo,
    SendVoice,
    TelegramMethod,
)

from homebot.services.prefs import Prefs
from homebot.services.quiet import current_importance, should_silence

_SILENCEABLE = (SendMessage, SendPhoto, SendDocument, SendMediaGroup, SendVoice, SendAudio, SendVideo, SendAnimation)


class QuietHoursMiddleware(BaseRequestMiddleware):
    def __init__(self, prefs: Prefs, tz: tzinfo) -> None:
        self._prefs = prefs
        self._tz = tz

    async def __call__(self, make_request: NextRequestMiddlewareType[Any], bot: Bot, method: TelegramMethod[Any]) -> Any:
        if (
            isinstance(method, _SILENCEABLE)
            and getattr(method, "disable_notification", None) is not True
            and should_silence(current_importance(), self._prefs.night(), datetime.now(self._tz).hour)
        ):
            method = method.model_copy(update={"disable_notification": True})
        return await make_request(bot, method)
