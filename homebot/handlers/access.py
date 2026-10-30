"""👥 Доступ: заявки и права людей (только для администратора)."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, MenuButtonCommands, MenuButtonWebApp, Message, WebAppInfo

from homebot.config import Settings
from homebot.services.access import APPROVED, BLOCKED, AccessService, Person
from homebot.ui import keyboards as kb
from homebot.ui.access_texts import GUEST_WELCOME, render_people, render_person, render_remove
from homebot.ui.callbacks import AccessAction, AccessCb
from homebot.utils.telegram import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="access")

_OFF = "👥 Доступ для других людей выключен."


async def _list(call_or_msg: CallbackQuery | Message, access: AccessService, settings: Settings) -> None:
    people = access.people()
    text, markup = render_people(people, settings.tz), kb.access_list_kb(people)
    if isinstance(call_or_msg, CallbackQuery):
        await edit_or_send(call_or_msg, text, markup)
    else:
        await call_or_msg.answer(text, reply_markup=markup)


async def _card(call: CallbackQuery, person: Person | None, access: AccessService, settings: Settings) -> None:
    if person is None:
        await call.answer("Человека уже нет в списке", show_alert=True)
        await _list(call, access, settings)
        return
    await edit_or_send(call, render_person(person, settings.tz), kb.person_kb(person))


@router.message(Command("access"))
async def cmd_access(message: Message, state: FSMContext, access: AccessService | None, settings: Settings) -> None:
    await state.clear()
    if access is None:
        await message.answer(_OFF)
        return
    await _list(message, access, settings)


@router.callback_query(AccessCb.filter(F.action == AccessAction.LIST))
async def cb_list(call: CallbackQuery, state: FSMContext, access: AccessService | None, settings: Settings) -> None:
    await state.clear()
    if access is None:
        await call.answer(_OFF, show_alert=True)
        return
    await call.answer()
    await _list(call, access, settings)


@router.callback_query(AccessCb.filter(F.action == AccessAction.VIEW))
async def cb_view(call: CallbackQuery, callback_data: AccessCb, access: AccessService | None, settings: Settings) -> None:
    if access is None:
        await call.answer(_OFF, show_alert=True)
        return
    await call.answer()
    await _card(call, access.get(callback_data.uid), access, settings)


@router.callback_query(AccessCb.filter(F.action == AccessAction.TOGGLE))
async def cb_toggle(call: CallbackQuery, callback_data: AccessCb, access: AccessService | None, settings: Settings) -> None:
    if access is None:
        await call.answer(_OFF, show_alert=True)
        return
    person = await access.toggle(callback_data.uid, callback_data.feat)
    await call.answer("Готово" if person else "Не получилось")
    await _card(call, person or access.get(callback_data.uid), access, settings)


@router.callback_query(AccessCb.filter(F.action.in_({AccessAction.APPROVE, AccessAction.BLOCK})))
async def cb_decide(call: CallbackQuery, callback_data: AccessCb, access: AccessService | None, settings: Settings) -> None:
    if access is None:
        await call.answer(_OFF, show_alert=True)
        return
    approve = callback_data.action == AccessAction.APPROVE
    person = await access.set_status(callback_data.uid, APPROVED if approve else BLOCKED)
    if person is None:
        await call.answer("Заявки уже нет", show_alert=True)
        await _list(call, access, settings)
        return
    await call.answer("Принят ✅" if approve else "Заблокирован 🚫")
    if not approve:
        await _reset_menu_button(call, person.id)
    if approve:
        await _welcome(call, person, settings)
    await _card(call, person, access, settings)


async def _welcome(call: CallbackQuery, person: Person, settings: Settings) -> None:
    """Сообщаем человеку, что доступ открыт (если он не успел заблокировать бота), и даём ему кнопку «Панель»."""
    bot = call.bot
    if bot is None:
        return
    web_url = settings.web_url if settings.web_enabled else ""
    try:
        await bot.send_message(person.id, GUEST_WELCOME, reply_markup=kb.guest_menu(person.perms, web_url))
        if web_url:
            await bot.set_chat_menu_button(
                chat_id=person.id, menu_button=MenuButtonWebApp(text="Панель", web_app=WebAppInfo(url=web_url))
            )
    except TelegramAPIError as exc:
        log.info("Cannot greet user %s: %s", person.id, exc)


async def _reset_menu_button(call: CallbackQuery, user_id: int) -> None:
    """Заблокировали или удалили человека — убираем у него кнопку «Панель»."""
    if call.bot is None:
        return
    try:
        await call.bot.set_chat_menu_button(chat_id=user_id, menu_button=MenuButtonCommands())
    except TelegramAPIError as exc:
        log.debug("Cannot reset menu button for %s: %s", user_id, exc)


@router.callback_query(AccessCb.filter(F.action == AccessAction.REMOVE))
async def cb_remove(call: CallbackQuery, callback_data: AccessCb, access: AccessService | None, settings: Settings) -> None:
    if access is None:
        await call.answer(_OFF, show_alert=True)
        return
    person = access.get(callback_data.uid)
    if person is None:
        await call.answer("Уже удалён")
        await _list(call, access, settings)
        return
    await call.answer()
    await edit_or_send(call, render_remove(person), kb.access_remove_kb(person.id))


@router.callback_query(AccessCb.filter(F.action == AccessAction.REMOVE_YES))
async def cb_remove_yes(call: CallbackQuery, callback_data: AccessCb, access: AccessService | None, settings: Settings) -> None:
    if access is None:
        await call.answer(_OFF, show_alert=True)
        return
    removed = await access.remove(callback_data.uid)
    if removed:
        await _reset_menu_button(call, callback_data.uid)
    await call.answer("Удалён" if removed else "Уже удалён")
    await _list(call, access, settings)
