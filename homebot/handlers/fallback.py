"""Последний роутер: всё, что не подошло остальным (устаревшие кнопки, случайный текст)."""

from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from homebot.ui.keyboards import main_menu

router = Router(name="fallback")


@router.message()
async def unknown_message(message: Message) -> None:
    await message.answer("🤔 Я не понял. Откройте меню: /menu", reply_markup=main_menu())


@router.callback_query()
async def stale_button(call: CallbackQuery) -> None:
    # Кнопки из старых сообщений (например, после перезапуска бота потерялись FSM-данные).
    await call.answer("Эта кнопка устарела — откройте /menu", show_alert=True)
