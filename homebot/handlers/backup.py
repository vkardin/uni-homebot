"""💾 Бэкап: сохранить сейчас, прислать файл, восстановить."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message

from homebot.config import Settings
from homebot.services import backup as svc
from homebot.services import usage
from homebot.services.db import Database
from homebot.services.state import StateStore
from homebot.ui import keyboards as kb
from homebot.ui.backup_texts import render_backup_home, render_restore_confirm
from homebot.ui.callbacks import BackupAction, BackupCb
from homebot.utils.telegram import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="backup")


def _extra(settings: Settings):
    return svc.parse_extra_paths(settings.backup_paths)


def _home(settings: Settings, store: StateStore) -> tuple[str, InlineKeyboardMarkup]:
    items = svc.list_backups(settings.state_dir)
    text = render_backup_home(
        items, svc.last_status(store), settings.backup_hour, settings.backup_keep, settings.tz, len(_extra(settings))
    )
    return text, kb.backup_kb(items, settings.tz)


@router.message(Command("backup"))
async def cmd_backup(message: Message, state: FSMContext, settings: Settings, store: StateStore) -> None:
    await state.clear()
    text, markup = _home(settings, store)
    await message.answer(text, reply_markup=markup)


@router.callback_query(BackupCb.filter(F.action == BackupAction.HOME))
async def cb_home(call: CallbackQuery, settings: Settings, store: StateStore) -> None:
    await call.answer()
    text, markup = _home(settings, store)
    await edit_or_send(call, text, markup)


@router.callback_query(BackupCb.filter(F.action == BackupAction.NOW))
async def cb_now(call: CallbackQuery, settings: Settings, store: StateStore, db: Database) -> None:
    await call.answer("💾 Сохраняю…")
    try:
        item = await svc.run_backup(settings.state_dir, db, store, keep=settings.backup_keep, extra=_extra(settings), tz=settings.tz)
    except svc.BackupError as exc:
        await call.answer(f"Не вышло: {exc}"[:190], show_alert=True)
        return
    text, markup = _home(settings, store)
    await edit_or_send(call, f"✅ Сохранено: {item.name}\n\n{text}", markup)


@router.callback_query(BackupCb.filter(F.action == BackupAction.SEND))
async def cb_send(call: CallbackQuery, callback_data: BackupCb, settings: Settings) -> None:
    items = svc.list_backups(settings.state_dir)
    chosen = next((i for i in items if i.ts == callback_data.ts), items[0] if items else None)
    if chosen is None or not isinstance(call.message, Message):
        await call.answer("Копий пока нет", show_alert=True)
        return
    await call.answer()
    await call.message.answer_document(BufferedInputFile(chosen.path.read_bytes(), chosen.name), caption="💾 Копия UNI (без .env)")


@router.callback_query(BackupCb.filter(F.action == BackupAction.RESTORE))
async def cb_restore(call: CallbackQuery, callback_data: BackupCb, settings: Settings, store: StateStore, db: Database) -> None:
    item = next((i for i in svc.list_backups(settings.state_dir) if i.ts == callback_data.ts), None)
    if item is None:
        await call.answer("Такой копии уже нет", show_alert=True)
        return
    if not callback_data.go:
        await call.answer()
        await edit_or_send(call, render_restore_confirm(item, settings.tz), kb.backup_confirm_kb(item.ts))
        return
    await call.answer("↩️ Восстанавливаю…")
    try:
        await svc.run_backup(settings.state_dir, db, store, keep=settings.backup_keep + 1, extra=[], tz=settings.tz)  # страховка
        done = await svc.restore_backup(item.path, settings.state_dir, db, store)
    except svc.BackupError as exc:
        await call.answer(f"Не вышло: {exc}"[:190], show_alert=True)
        return
    text, markup = _home(settings, store)
    await edit_or_send(call, f"✅ Восстановлено: {', '.join(done)}. Прежнее состояние сохранено отдельной копией.\n\n{text}", markup)


@router.message(Command("usage"))
async def cmd_usage(message: Message, store: StateStore) -> None:
    await message.answer(usage.render(usage.top(store)))
