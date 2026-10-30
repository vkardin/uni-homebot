"""Журналы служб и их разбор моделью."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender

from homebot.services.logs import EXPLAIN_PROMPT, LogsService
from homebot.services.ollama import OllamaError, OllamaService
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import LogAction, LogCb, MenuAction, MenuCb
from homebot.ui.texts import render_log, render_ollama_error
from homebot.utils.formatting import split_message
from homebot.utils.telegram import answer_from_callback, edit_or_send

log = logging.getLogger(__name__)
router = Router(name="logs")
_MENU_TEXT = "📜 <b>Журналы</b>\nВыберите службу — покажу последние 40 строк, а модель может их разобрать."


@router.message(Command("logs"))
async def cmd_logs(message: Message, state: FSMContext, logs: LogsService) -> None:
    await state.clear()
    await message.answer(_MENU_TEXT, reply_markup=kb.logs_menu_kb(logs.units))


@router.callback_query(MenuCb.filter(F.action == MenuAction.LOGS))
async def cb_logs(call: CallbackQuery, state: FSMContext, logs: LogsService) -> None:
    await call.answer()
    await state.clear()
    await edit_or_send(call, _MENU_TEXT, kb.logs_menu_kb(logs.units))


@router.callback_query(LogCb.filter(F.action == LogAction.SHOW))
async def cb_show(call: CallbackQuery, callback_data: LogCb, logs: LogsService) -> None:
    if not 0 <= callback_data.idx < len(logs.units):
        await call.answer("Список служб изменился — откройте заново", show_alert=True)
        return
    await call.answer()
    unit = logs.units[callback_data.idx]
    text, error = await logs.tail(unit)
    await edit_or_send(call, render_log(unit, text, error), kb.log_view_kb(callback_data.idx))


@router.callback_query(LogCb.filter(F.action == LogAction.EXPLAIN))
async def cb_explain(call: CallbackQuery, callback_data: LogCb, logs: LogsService, ollama: OllamaService, bot: Bot) -> None:
    if not 0 <= callback_data.idx < len(logs.units):
        await call.answer("Список служб изменился — откройте заново", show_alert=True)
        return
    unit = logs.units[callback_data.idx]
    text, error = await logs.tail(unit, lines=80)
    if error or not text:
        await call.answer(f"Нечего разбирать: {error or 'журнал пуст'}"[:200], show_alert=True)
        return
    await call.answer("🧠 Разбираю журнал…")
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=call.from_user.id):
            answer = await ollama.ask(EXPLAIN_PROMPT, f"Служба: {unit}\n\n{text}")
    except OllamaError as exc:
        log.warning("Log explanation failed: %s", exc)
        await answer_from_callback(call, render_ollama_error(exc))
        return
    for chunk in split_message(f"🧠 Разбор журнала {unit}\n\n{answer}") or ["(пустой ответ)"]:
        await answer_from_callback(call, chunk, parse_mode=None)
