"""Напоминания и заметки."""

from __future__ import annotations

import html
import time
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from homebot.config import Settings
from homebot.services.db import Database
from homebot.services.reminders import describe_rule, parse_recurring, parse_reminder
from homebot.states import NoteStates, ReminderStates
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import ItemAction, MenuAction, MenuCb, NoteCb, ReminderCb
from homebot.ui.texts import NOTE_HELP, REMINDER_HELP, render_notes, render_reminders
from homebot.utils.telegram import edit_or_send

router = Router(name="reminders")


# ------------------------------------------------------------------ напоминания
async def _reminders_view(db: Database, settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    items = await db.pending_reminders()
    return render_reminders(items, settings.tz), kb.reminders_kb(items)


async def _create_reminder(message: Message, text: str, db: Database, settings: Settings) -> bool:
    now = datetime.now(settings.tz)
    parsed = parse_recurring(text, now) or parse_reminder(text, now)
    if parsed is None:
        await message.answer(
            "🤔 Не понял, когда напомнить (или это время уже прошло).\n\n" + REMINDER_HELP,
            reply_markup=kb.back_kb(MenuAction.REMINDERS, "❌ Отмена"),
        )
        return False
    await db.add_reminder(int(parsed.due.timestamp()), parsed.text, parsed.rule)
    when = f"{describe_rule(parsed.rule)}, ближайшее {parsed.due:%d.%m в %H:%M}" if parsed.rule else f"{parsed.due:%d.%m в %H:%M}"
    await message.answer(
        f"✅ Напомню <b>{when}</b>: {html.escape(parsed.text)}",
        reply_markup=kb.back_kb(MenuAction.REMINDERS, "⏰ Все напоминания"),
    )
    return True


@router.message(Command("remind"))
async def cmd_remind(message: Message, command: CommandObject, state: FSMContext, db: Database, settings: Settings) -> None:
    if not command.args:
        await state.set_state(ReminderStates.add)
        await message.answer(REMINDER_HELP, reply_markup=kb.back_kb(MenuAction.REMINDERS, "❌ Отмена"))
        return
    await state.clear()
    await _create_reminder(message, command.args, db, settings)


@router.message(ReminderStates.add, ~F.text.startswith("/"))  # команды проходят к своим обработчикам
async def on_reminder_text(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    if not message.text:
        await message.answer(REMINDER_HELP)
        return
    if await _create_reminder(message, message.text, db, settings):
        await state.clear()


@router.message(Command("reminders"))
async def cmd_reminders(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    await state.clear()
    text, markup = await _reminders_view(db, settings)
    await message.answer(text, reply_markup=markup)


@router.callback_query(MenuCb.filter(F.action == MenuAction.REMINDERS))
async def cb_reminders(call: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    await call.answer()
    await state.clear()
    text, markup = await _reminders_view(db, settings)
    await edit_or_send(call, text, markup)


@router.callback_query(ReminderCb.filter(F.action == ItemAction.ADD))
async def cb_reminder_add(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(ReminderStates.add)
    await edit_or_send(call, REMINDER_HELP, kb.back_kb(MenuAction.REMINDERS, "❌ Отмена"))


@router.callback_query(ReminderCb.filter(F.action == ItemAction.DELETE))
async def cb_reminder_delete(call: CallbackQuery, callback_data: ReminderCb, db: Database, settings: Settings) -> None:
    deleted = await db.delete_reminder(callback_data.id)
    await call.answer("Удалено" if deleted else "Уже удалено")
    text, markup = await _reminders_view(db, settings)
    await edit_or_send(call, text, markup)


# --------------------------------------------------------------------- заметки
async def _notes_view(db: Database, settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    items = await db.list_notes()
    return render_notes(items, settings.tz), kb.notes_kb(items)


async def _create_note(message: Message, text: str, db: Database) -> None:
    await db.add_note(int(time.time()), text.strip()[:2000])
    await message.answer("📝 Заметка сохранена", reply_markup=kb.back_kb(MenuAction.NOTES, "📝 Все заметки"))


@router.message(Command("note"))
async def cmd_note(message: Message, command: CommandObject, state: FSMContext, db: Database) -> None:
    if not command.args:
        await state.set_state(NoteStates.add)
        await message.answer(NOTE_HELP, reply_markup=kb.back_kb(MenuAction.NOTES, "❌ Отмена"))
        return
    await state.clear()
    await _create_note(message, command.args, db)


@router.message(NoteStates.add, ~F.text.startswith("/"))
async def on_note_text(message: Message, state: FSMContext, db: Database) -> None:
    text = message.text or message.caption
    if not text:
        await message.answer(NOTE_HELP)
        return
    await state.clear()
    await _create_note(message, text, db)


@router.message(Command("notes"))
async def cmd_notes(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    await state.clear()
    text, markup = await _notes_view(db, settings)
    await message.answer(text, reply_markup=markup)


@router.callback_query(MenuCb.filter(F.action == MenuAction.NOTES))
async def cb_notes(call: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    await call.answer()
    await state.clear()
    text, markup = await _notes_view(db, settings)
    await edit_or_send(call, text, markup)


@router.callback_query(NoteCb.filter(F.action == ItemAction.ADD))
async def cb_note_add(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(NoteStates.add)
    await edit_or_send(call, NOTE_HELP, kb.back_kb(MenuAction.NOTES, "❌ Отмена"))


@router.callback_query(NoteCb.filter(F.action == ItemAction.DELETE))
async def cb_note_delete(call: CallbackQuery, callback_data: NoteCb, db: Database, settings: Settings) -> None:
    deleted = await db.delete_note(callback_data.id)
    await call.answer("Удалено" if deleted else "Уже удалено")
    text, markup = await _notes_view(db, settings)
    await edit_or_send(call, text, markup)
