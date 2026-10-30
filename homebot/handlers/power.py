"""Перезапуск служб и питание сервера — только с подтверждением, которое действует 60 секунд."""

from __future__ import annotations

import html
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from homebot.services.power import PowerService, is_fresh
from homebot.services.units import UnitsService
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, MenuCb, PowerAction, PowerCb
from homebot.ui.texts import render_confirm, render_power
from homebot.utils.telegram import edit_or_send

router = Router(name="power")


def _menu(power: PowerService) -> tuple[str, InlineKeyboardMarkup]:
    return (
        render_power(power.restartable, power_enabled=power.power_enabled),
        kb.power_kb(power.restartable, power_enabled=power.power_enabled),
    )


@router.message(Command("power"))
async def cmd_power(message: Message, state: FSMContext, power: PowerService) -> None:
    await state.clear()
    text, markup = _menu(power)
    await message.answer(text, reply_markup=markup)


@router.callback_query(MenuCb.filter(F.action == MenuAction.POWER))
async def cb_power_menu(call: CallbackQuery, state: FSMContext, power: PowerService) -> None:
    await call.answer()
    await state.clear()
    text, markup = _menu(power)
    await edit_or_send(call, text, markup)


@router.callback_query(PowerCb.filter())
async def cb_power(call: CallbackQuery, callback_data: PowerCb, power: PowerService, units: UnitsService) -> None:
    action = callback_data.action
    unit = ""
    if action == PowerAction.RESTART:
        if not 0 <= callback_data.idx < len(power.restartable):
            await call.answer("Список служб изменился — откройте меню заново", show_alert=True)
            return
        unit = power.restartable[callback_data.idx]
        question = f"Перезапустить {unit}?"
    elif not power.power_enabled:
        await call.answer("Управление питанием отключено (POWER_ENABLED=false)", show_alert=True)
        return
    elif action == PowerAction.REBOOT:
        question = "Перезагрузить сервер? Бот вернётся через 1–2 минуты."
    else:
        question = "Выключить сервер? Включить его удалённо будет нельзя!"

    if not callback_data.go:
        await call.answer()
        ts = int(time.time())
        await edit_or_send(call, render_confirm(question), kb.confirm_kb(action, callback_data.idx, ts))
        return

    if not is_fresh(callback_data.ts, time.time()):
        await call.answer("Подтверждение устарело — повторите", show_alert=True)
        text, markup = _menu(power)
        await edit_or_send(call, text, markup)
        return

    await call.answer("Выполняю…")
    text, markup = _menu(power)
    if action == PowerAction.RESTART:
        await edit_or_send(call, f"⏳ Перезапускаю <code>{html.escape(unit)}</code>…")
        result = await power.restart(unit)
        if result.ok:
            status = await units.status(unit)
            summary = f"✅ <code>{html.escape(unit)}</code> перезапущена: {status.emoji} {html.escape(status.label)}"
        else:
            summary = f"❌ Не удалось перезапустить <code>{html.escape(unit)}</code>: {html.escape(result.message)}"
        await edit_or_send(call, f"{summary}\n\n{text}", markup)
        return

    # Сообщаем ДО команды: после reboot/poweroff бот уже не успеет ответить.
    verb = "♻️ Перезагружаю сервер…" if action == PowerAction.REBOOT else "⏻ Выключаю сервер…"
    await edit_or_send(call, verb)
    result = await (power.reboot() if action == PowerAction.REBOOT else power.poweroff())
    if not result.ok:
        await edit_or_send(call, f"❌ Не удалось: {html.escape(result.message)}\n\n{text}", markup)
