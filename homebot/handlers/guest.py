"""👤 Гости: принятые администратором люди. Видят только разрешённые им функции и только свои данные.

Роутер стоит первым и забирает ВСЁ от гостей (включая запасной «не понял»), поэтому события гостя
никогда не доходят до админских роутеров. Админских обработчиков этот роутер не касается.
"""

from __future__ import annotations

import html
import time
from datetime import datetime
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from homebot.config import Settings
from homebot.services.access import LIMITS, Person
from homebot.services.apps import Apps
from homebot.services.apps_http import AppError
from homebot.services.db import Database
from homebot.services.p2p import P2PQuote
from homebot.services.reminders import describe_rule, parse_recurring, parse_reminder
from homebot.states import NoteStates, ReminderStates
from homebot.ui import keyboards as kb
from homebot.ui.access_texts import GUEST_DENIED, GUEST_LIMIT, GUEST_NOTHING, GUEST_UNKNOWN, GUEST_WELCOME
from homebot.ui.callbacks import ItemAction, MenuAction, MenuCb, NoteCb, ReminderCb
from homebot.ui.p2p_texts import money
from homebot.ui.style import DIV
from homebot.ui.texts import NOTE_HELP, REMINDER_HELP, esc, render_notes, render_reminders
from homebot.utils.formatting import truncate
from homebot.utils.telegram import edit_or_send

router = Router(name="guest")


async def is_guest(event: Any, guest: Person | None = None) -> bool:
    """Фильтр роутера: событие от принятого гостя (админу `guest` — None). aiogram первым аргументом передаёт само событие."""
    return guest is not None


async def not_guest(event: Any, guest: Person | None = None) -> bool:
    """Фильтр админских роутеров: гость туда не попадает ни при каких обстоятельствах."""
    return guest is None


router.message.filter(is_guest)
router.callback_query.filter(is_guest)


def _menu_view(guest: Person, settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    markup = kb.guest_menu(guest.perms, settings.web_url if settings.web_enabled else "")
    return (GUEST_WELCOME if guest.perms else f"🤖 <b>UNI</b>\n{DIV}\n{GUEST_NOTHING}"), markup


def _back(text: str = "⬅️ Меню") -> InlineKeyboardMarkup:
    return kb.back_kb(MenuAction.HOME, text)


# ----------------------------------------------------------------------- меню
@router.message(Command("start", "menu", "help"))
async def g_menu(message: Message, state: FSMContext, guest: Person, settings: Settings) -> None:
    await state.clear()
    text, markup = _menu_view(guest, settings)
    await message.answer(text, reply_markup=markup)


@router.message(Command("cancel"))
async def g_cancel(message: Message, state: FSMContext, guest: Person, settings: Settings) -> None:
    await state.clear()
    text, markup = _menu_view(guest, settings)
    await message.answer(f"Готово, вышел из режима 👌\n\n{text}", reply_markup=markup)


@router.callback_query(MenuCb.filter(F.action == MenuAction.HOME))
async def g_home(call: CallbackQuery, state: FSMContext, guest: Person, settings: Settings) -> None:
    await call.answer()
    await state.clear()
    text, markup = _menu_view(guest, settings)
    await edit_or_send(call, text, markup)


async def _deny(target: Message | CallbackQuery) -> None:
    if isinstance(target, CallbackQuery):
        await target.answer(GUEST_DENIED, show_alert=True)
    else:
        await target.answer(GUEST_DENIED)


# ------------------------------------------------------------------ заметки
async def _notes_view(db: Database, guest: Person, settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    items = await db.list_notes(owner=guest.id)
    return render_notes(items, settings.tz), kb.notes_kb(items)


async def _create_note(message: Message, text: str, db: Database, guest: Person) -> None:
    limit = LIMITS["notes"]
    if await db.count_notes(guest.id) >= limit:
        await message.answer(GUEST_LIMIT.format(limit=limit), reply_markup=kb.back_kb(MenuAction.NOTES, "📝 Все заметки"))
        return
    await db.add_note(int(time.time()), text.strip()[:2000], owner=guest.id)
    await message.answer("📝 Заметка сохранена", reply_markup=kb.back_kb(MenuAction.NOTES, "📝 Все заметки"))


@router.message(Command("note"))
async def g_cmd_note(message: Message, command: CommandObject, state: FSMContext, db: Database, guest: Person) -> None:
    if not guest.can("notes"):
        return await _deny(message)
    if not command.args:
        await state.set_state(NoteStates.add)
        await message.answer(NOTE_HELP, reply_markup=kb.back_kb(MenuAction.NOTES, "❌ Отмена"))
        return None
    await state.clear()
    await _create_note(message, command.args, db, guest)
    return None


@router.message(NoteStates.add, ~F.text.startswith("/"))
async def g_note_text(message: Message, state: FSMContext, db: Database, guest: Person) -> None:
    text = message.text or message.caption
    if not guest.can("notes"):
        await state.clear()
        return await _deny(message)
    if not text:
        await message.answer(NOTE_HELP)
        return None
    await state.clear()
    await _create_note(message, text, db, guest)
    return None


@router.message(Command("notes"))
async def g_cmd_notes(message: Message, state: FSMContext, db: Database, settings: Settings, guest: Person) -> None:
    await state.clear()
    if not guest.can("notes"):
        return await _deny(message)
    text, markup = await _notes_view(db, guest, settings)
    await message.answer(text, reply_markup=markup)
    return None


@router.callback_query(MenuCb.filter(F.action == MenuAction.NOTES))
async def g_cb_notes(call: CallbackQuery, state: FSMContext, db: Database, settings: Settings, guest: Person) -> None:
    await state.clear()
    if not guest.can("notes"):
        return await _deny(call)
    await call.answer()
    text, markup = await _notes_view(db, guest, settings)
    await edit_or_send(call, text, markup)
    return None


@router.callback_query(NoteCb.filter(F.action == ItemAction.ADD))
async def g_cb_note_add(call: CallbackQuery, state: FSMContext, guest: Person) -> None:
    if not guest.can("notes"):
        return await _deny(call)
    await call.answer()
    await state.set_state(NoteStates.add)
    await edit_or_send(call, NOTE_HELP, kb.back_kb(MenuAction.NOTES, "❌ Отмена"))
    return None


@router.callback_query(NoteCb.filter(F.action == ItemAction.DELETE))
async def g_cb_note_delete(call: CallbackQuery, callback_data: NoteCb, db: Database, settings: Settings, guest: Person) -> None:
    if not guest.can("notes"):
        return await _deny(call)
    deleted = await db.delete_note(callback_data.id, owner=guest.id)
    await call.answer("Удалено" if deleted else "Уже удалено")
    text, markup = await _notes_view(db, guest, settings)
    await edit_or_send(call, text, markup)
    return None


# ------------------------------------------------------------- напоминания
async def _reminders_view(db: Database, guest: Person, settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    items = await db.pending_reminders(owner=guest.id)
    return render_reminders(items, settings.tz), kb.reminders_kb(items)


async def _create_reminder(message: Message, text: str, db: Database, settings: Settings, guest: Person) -> bool:
    limit = LIMITS["reminders"]
    if await db.count_reminders(guest.id) >= limit:
        await message.answer(GUEST_LIMIT.format(limit=limit), reply_markup=kb.back_kb(MenuAction.REMINDERS, "⏰ Все напоминания"))
        return True
    now = datetime.now(settings.tz)
    parsed = parse_recurring(text, now) or parse_reminder(text, now)
    if parsed is None:
        await message.answer(
            "🤔 Не понял, когда напомнить (или это время уже прошло).\n\n" + REMINDER_HELP,
            reply_markup=kb.back_kb(MenuAction.REMINDERS, "❌ Отмена"),
        )
        return False
    await db.add_reminder(int(parsed.due.timestamp()), parsed.text[:500], parsed.rule, owner=guest.id)
    when = f"{describe_rule(parsed.rule)}, ближайшее {parsed.due:%d.%m в %H:%M}" if parsed.rule else f"{parsed.due:%d.%m в %H:%M}"
    await message.answer(
        f"✅ Напомню <b>{when}</b>: {html.escape(parsed.text[:500])}",
        reply_markup=kb.back_kb(MenuAction.REMINDERS, "⏰ Все напоминания"),
    )
    return True


@router.message(Command("remind"))
async def g_cmd_remind(
    message: Message, command: CommandObject, state: FSMContext, db: Database, settings: Settings, guest: Person
) -> None:
    if not guest.can("reminders"):
        return await _deny(message)
    if not command.args:
        await state.set_state(ReminderStates.add)
        await message.answer(REMINDER_HELP, reply_markup=kb.back_kb(MenuAction.REMINDERS, "❌ Отмена"))
        return None
    await state.clear()
    await _create_reminder(message, command.args, db, settings, guest)
    return None


@router.message(ReminderStates.add, ~F.text.startswith("/"))
async def g_reminder_text(message: Message, state: FSMContext, db: Database, settings: Settings, guest: Person) -> None:
    if not guest.can("reminders"):
        await state.clear()
        return await _deny(message)
    if not message.text:
        await message.answer(REMINDER_HELP)
        return None
    if await _create_reminder(message, message.text, db, settings, guest):
        await state.clear()
    return None


@router.message(Command("reminders"))
async def g_cmd_reminders(message: Message, state: FSMContext, db: Database, settings: Settings, guest: Person) -> None:
    await state.clear()
    if not guest.can("reminders"):
        return await _deny(message)
    text, markup = await _reminders_view(db, guest, settings)
    await message.answer(text, reply_markup=markup)
    return None


@router.callback_query(MenuCb.filter(F.action == MenuAction.REMINDERS))
async def g_cb_reminders(call: CallbackQuery, state: FSMContext, db: Database, settings: Settings, guest: Person) -> None:
    await state.clear()
    if not guest.can("reminders"):
        return await _deny(call)
    await call.answer()
    text, markup = await _reminders_view(db, guest, settings)
    await edit_or_send(call, text, markup)
    return None


@router.callback_query(ReminderCb.filter(F.action == ItemAction.ADD))
async def g_cb_reminder_add(call: CallbackQuery, state: FSMContext, guest: Person) -> None:
    if not guest.can("reminders"):
        return await _deny(call)
    await call.answer()
    await state.set_state(ReminderStates.add)
    await edit_or_send(call, REMINDER_HELP, kb.back_kb(MenuAction.REMINDERS, "❌ Отмена"))
    return None


@router.callback_query(ReminderCb.filter(F.action == ItemAction.DELETE))
async def g_cb_reminder_delete(
    call: CallbackQuery, callback_data: ReminderCb, db: Database, settings: Settings, guest: Person
) -> None:
    if not guest.can("reminders"):
        return await _deny(call)
    deleted = await db.delete_reminder(callback_data.id, owner=guest.id)
    await call.answer("Удалено" if deleted else "Уже удалено")
    text, markup = await _reminders_view(db, guest, settings)
    await edit_or_send(call, text, markup)
    return None


# ----------------------------------------------------------------------- P2P
def render_guest_p2p(quote: P2PQuote | None, error: str, *, asset: str, fiat: str) -> str:
    lines = [f"💱 <b>Продать {esc(asset)} за {esc(fiat)}</b> · Binance P2P", DIV]
    if quote is None:
        lines.append(f"⚠️ Не получилось получить курс: {esc(error)}" if error else "Курс ещё не получен.")
    else:
        lines += [f"💵 Типичная цена: <b>{money(quote.typical, fiat)}</b>", f"🥇 Лучшая: <b>{money(quote.best, fiat)}</b>"]
    return "\n".join(lines)


async def _p2p_view(apps: Apps) -> str:
    service = apps.p2p
    if service is None:
        return "💱 Курс P2P на этом сервере не подключён."
    try:
        return render_guest_p2p(await service.quote(), "", asset=service.asset, fiat=service.fiat)
    except AppError as exc:
        return render_guest_p2p(None, str(exc), asset=service.asset, fiat=service.fiat)


@router.message(Command("p2p"))
async def g_cmd_p2p(message: Message, state: FSMContext, apps: Apps, guest: Person) -> None:
    await state.clear()
    if not guest.can("p2p"):
        return await _deny(message)
    await message.answer(await _p2p_view(apps), reply_markup=_back())
    return None


@router.callback_query(MenuCb.filter(F.action == MenuAction.P2P))
async def g_cb_p2p(call: CallbackQuery, state: FSMContext, apps: Apps, guest: Person) -> None:
    await state.clear()
    if not guest.can("p2p"):
        return await _deny(call)
    await call.answer()
    await edit_or_send(call, await _p2p_view(apps), _back())
    return None


# -------------------------------------------------------------------- новости
async def _news_view(db: Database, settings: Settings) -> str:
    items = await db.recent_sent_news(8)
    lines = ["🗞 <b>Новости</b>", DIV]
    if not items:
        lines.append("Пока свежих новостей нет.")
    for item in items:
        when = datetime.fromtimestamp(item.sent_ts, settings.tz).strftime("%d.%m %H:%M")
        title = esc(truncate(item.title, 110))
        lines.append(
            f'• <i>{when}</i> <a href="{html.escape(item.link, quote=True)}">{title}</a>' if item.link else f"• <i>{when}</i> {title}"
        )
    return "\n".join(lines)


async def _send_news(target: Message | CallbackQuery, db: Database, settings: Settings) -> None:
    text = await _news_view(db, settings)
    if isinstance(target, CallbackQuery):
        await target.answer()
        await edit_or_send(target, text, _back())
    else:
        await target.answer(text, reply_markup=_back(), disable_web_page_preview=True)


@router.message(Command("news"))
async def g_cmd_news(message: Message, state: FSMContext, db: Database, settings: Settings, guest: Person) -> None:
    await state.clear()
    if not guest.can("news"):
        return await _deny(message)
    await _send_news(message, db, settings)
    return None


@router.callback_query(MenuCb.filter(F.action == MenuAction.NEWS))
async def g_cb_news(call: CallbackQuery, state: FSMContext, db: Database, settings: Settings, guest: Person) -> None:
    await state.clear()
    if not guest.can("news"):
        return await _deny(call)
    await _send_news(call, db, settings)
    return None


# ------------------------------------------------- всё остальное — не для гостей
@router.message()
async def g_unknown_message(message: Message, guest: Person, settings: Settings) -> None:
    text, markup = _menu_view(guest, settings)
    await message.answer(f"{GUEST_UNKNOWN}\n\n{text}", reply_markup=markup)


@router.callback_query()
async def g_stale(call: CallbackQuery, guest: Person) -> None:
    await call.answer("Эта кнопка устарела или недоступна — открой /menu", show_alert=True)
