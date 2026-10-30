"""Доступ к боту: администратор (ADMIN_ID) — всё, остальные — только то, что он им разрешил."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Update, User

from homebot.services.access import APPROVED, BLOCKED, AccessService, Person
from homebot.ui.access_texts import GUEST_PENDING, render_request

log = logging.getLogger(__name__)

# Из каких полей Update достаём автора события (запасной путь, если aiogram не положил
# `event_from_user` в data).
_UPDATE_FIELDS = (
    "message",
    "edited_message",
    "callback_query",
    "inline_query",
    "chosen_inline_result",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "pre_checkout_query",
    "shipping_query",
)


class AdminOnlyMiddleware(BaseMiddleware):
    """Outer-middleware на уровне `Update`: решает, кто и что может в боте.

    * Администратор (ADMIN_ID) — всё. В `data["guest"]` у него `None`.
    * Остальные — только если есть `AccessService`: первое обращение превращается в заявку,
      админу приходит сообщение с кнопками; пока заявка не принята (или человек заблокирован) бот молчит.
      Принятый попадает в обработчики с `data["guest"] = Person`; admin-роутеры его не пропускают.
    * Fail-closed: если автора определить не удалось — апдейт блокируется.
    * Работает только в личном чате с ботом (в группах и каналах бот не отвечает).
    * Без `AccessService` чужим бот не отвечает вообще. В лог пишется не чаще раза в `log_interval` секунд на ID.
    """

    def __init__(self, admin_id: int, access: AccessService | None = None, *, log_interval: float = 600.0) -> None:
        self._admin_id = admin_id
        self._access = access
        self._log_interval = log_interval
        self._last_logged: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        user = self._resolve_user(event, data)
        if user is None:
            self._log_blocked(event, user)
            return None

        chat = self._resolve_chat(event, data)
        is_admin = user.id == self._admin_id
        if chat is None or chat.type != ChatType.PRIVATE:
            # Fail-closed и здесь: чат неизвестен или это группа/канал — молчим, чтобы метрики
            # сервера не попали в общий чат, если бота туда случайно добавили.
            if is_admin:
                log.warning(
                    "Ignored update %s from admin outside a private chat (%s)", event.update_id, getattr(chat, "type", "unknown")
                )
            else:
                self._log_blocked(event, user)
            return None

        if is_admin:
            data["guest"] = None
            return await handler(event, data)

        person = await self._gate(event, user, data)
        if person is None:
            return None
        data["guest"] = person
        return await handler(event, data)

    async def _gate(self, event: Update, user: User, data: dict[str, Any]) -> Person | None:
        """Принятый гость — `Person`; всем остальным (нет сервиса, заявка, блок) — `None`."""
        access = self._access
        if access is None:
            self._log_blocked(event, user)
            return None
        try:
            person = access.get(user.id)
            if person is None:
                if not (getattr(event, "message", None) or getattr(event, "callback_query", None)):
                    return None  # заявку создаёт только живое обращение, а не служебное событие (например, блокировка бота)
                person, created = await access.register(user.id, user.full_name, user.username)
                if person is None:  # заявок слишком много — молчим
                    self._log_blocked(event, user)
                    return None
                if created:
                    log.info("Access request from user_id=%s", user.id)
                    await self._notify_admin(data, person)
                    await self._say(data, user.id, GUEST_PENDING)
                    return None
            if person.status == BLOCKED:
                self._log_blocked(event, user)
                return None
            if person.status != APPROVED:
                if access.should_remind(user.id):
                    await self._say(data, user.id, GUEST_PENDING)
                return None
            await access.touch(user.id)
            return person
        except Exception:
            log.exception("Access gate failed for user_id=%s", user.id)
            return None  # fail-closed

    async def _notify_admin(self, data: dict[str, Any], person: Person) -> None:
        from homebot.ui.keyboards import access_request_kb  # лениво: клавиатуры тянут весь UI-слой

        bot = data.get("bot")
        if bot is None:
            return
        try:
            await bot.send_message(self._admin_id, render_request(person), reply_markup=access_request_kb(person.id))
        except TelegramAPIError as exc:
            log.warning("Cannot notify admin about access request: %s", exc)

    @staticmethod
    async def _say(data: dict[str, Any], chat_id: int, text: str) -> None:
        bot = data.get("bot")
        if bot is None:
            return
        try:
            await bot.send_message(chat_id, text)
        except TelegramAPIError as exc:
            log.debug("Cannot answer user %s: %s", chat_id, exc)

    @staticmethod
    def _resolve_user(event: Update, data: dict[str, Any]) -> User | None:
        user = data.get("event_from_user")
        if user is not None:
            return user  # type: ignore[no-any-return]
        for field in _UPDATE_FIELDS:
            inner = getattr(event, field, None)
            if inner is not None:
                return getattr(inner, "from_user", None)
        return None

    @staticmethod
    def _resolve_chat(event: Update, data: dict[str, Any]) -> Any:
        chat = data.get("event_chat")
        if chat is not None:
            return chat
        for field in _UPDATE_FIELDS:
            inner = getattr(event, field, None)
            if inner is None:
                continue
            # У callback_query чат лежит в `.message.chat`, у остальных — в `.chat`.
            inner_chat = getattr(inner, "chat", None)
            if inner_chat is None:
                inner_chat = getattr(getattr(inner, "message", None), "chat", None)
            return inner_chat
        return None

    def _log_blocked(self, event: Update, user: User | None) -> None:
        user_id = user.id if user is not None else 0
        now = time.monotonic()
        last = self._last_logged.get(user_id)
        if last is not None and now - last < self._log_interval:
            return
        # Ограничиваем размер словаря, чтобы флуд с разных ID не раздувал память.
        if len(self._last_logged) > 1000:
            self._last_logged.clear()
        self._last_logged[user_id] = now
        log.warning("Blocked update %s from user_id=%s", event.update_id, user_id or "unknown")
