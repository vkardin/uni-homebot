"""💾 Бэкап: сохранить сейчас, прислать файл, восстановить; 📦 перенос настроек (/export и присланный файл)."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from homebot.config import Settings
from homebot.services import backup as svc
from homebot.services import transfer, usage
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


# ------------------------------------------------------------------ 📦 перенос настроек
PENDING_IMPORT = "import-settings.json"


@router.message(Command("export"))
async def cmd_export(message: Message, settings: Settings, store: StateStore) -> None:
    raw = transfer.export(settings.state_dir, store)
    await message.answer_document(
        BufferedInputFile(raw, transfer.FILE_NAME),
        caption="📦 Настройки UNI для переноса. На новом сервере просто пришлите этот файл боту.\n"
        "Токены, ключи приложений и данные сюда не входят.",
    )


@router.message(F.document, lambda m: bool(m.document and transfer.is_settings_file(m.document.file_name)))
async def on_settings_file(message: Message, bot: Bot, settings: Settings) -> None:
    doc = message.document
    if doc is None or (doc.file_size or 0) > transfer.MAX_SIZE:
        await message.answer("Файл слишком большой для настроек UNI.")
        return
    buf = await bot.download(doc)
    raw = buf.read() if buf is not None else b""
    try:
        parsed = transfer.parse(raw)
    except transfer.TransferError as exc:
        await message.answer(f"Не получилось: {exc}.")
        return
    (settings.state_dir / PENDING_IMPORT).write_bytes(raw)
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Применить", callback_data=BackupCb(action=BackupAction.IMPORT, go=1).pack())],
            [InlineKeyboardButton(text="Отмена", callback_data=BackupCb(action=BackupAction.IMPORT, go=0).pack())],
        ]
    )
    await message.answer(
        f"📦 Настройки UNI {parsed['version']}: настроек панели {len(parsed['settings'])}, "
        f"разделов {len(parsed['state'])}.\nПрименить поверх текущих? Перед этим сохраню бэкап.",
        reply_markup=markup,
    )


@router.callback_query(BackupCb.filter(F.action == BackupAction.IMPORT))
async def cb_import(call: CallbackQuery, callback_data: BackupCb, settings: Settings, store: StateStore, db: Database) -> None:
    pending = settings.state_dir / PENDING_IMPORT
    if not callback_data.go or not pending.is_file():
        pending.unlink(missing_ok=True)
        await call.answer("Отменено" if not callback_data.go else "Файла уже нет, пришлите заново")
        await edit_or_send(call, "📦 Перенос отменён.")
        return
    await call.answer("Применяю…")
    try:
        await svc.run_backup(settings.state_dir, db, store, keep=settings.backup_keep + 1, extra=[], tz=settings.tz)  # страховка
        done = await transfer.apply(transfer.parse(pending.read_bytes()), settings.state_dir, store)
    except (svc.BackupError, transfer.TransferError, OSError) as exc:
        await edit_or_send(call, f"Не получилось: {exc}")
        return
    finally:
        pending.unlink(missing_ok=True)
    await edit_or_send(
        call,
        f"✅ Перенесено: {', '.join(done) or 'нечего'}.\nПеред этим сохранён бэкап. "
        "Чтобы всё применилось, перезапустите бота: 🛠 /repair или Настройки в панели.",
    )


@router.message(Command("usage"))
async def cmd_usage(message: Message, store: StateStore) -> None:
    await message.answer(usage.render(usage.top(store)))
