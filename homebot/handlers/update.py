"""⬆️ Обновление из чата: пришлите архив homebot-X.Y.Z.tar.gz — бот проверит архив и (по кнопке) поставит через обновлятор."""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from homebot import __version__
from homebot.config import Settings
from homebot.services import backup as backup_svc
from homebot.services import selfupdate
from homebot.services.db import Database
from homebot.services.power import is_fresh
from homebot.services.state import StateStore
from homebot.ui import keyboards as kb
from homebot.ui import update_texts as texts
from homebot.ui.callbacks import UpdateAction, UpdateCb
from homebot.utils.telegram import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="update")

_watchdogs: set[asyncio.Task[None]] = set()
WATCHDOG_SEC = 240


def _is_archive(message: Message) -> bool:
    document = message.document
    return bool(document and document.file_name and document.file_name.lower().endswith((".tar.gz", ".tgz")))


def _home() -> tuple[str, object]:
    previous = selfupdate.previous_version()
    text = texts.render_update_home(__version__, ready=selfupdate.updater_installed(), previous=previous)
    return text, kb.update_kb(previous=previous)


@router.message(Command("update"))
async def cmd_update(message: Message) -> None:
    text, markup = _home()
    await message.answer(text, reply_markup=markup)  # type: ignore[arg-type]


@router.callback_query(UpdateCb.filter(F.action == UpdateAction.INFO))
async def cb_info(call: CallbackQuery) -> None:
    await call.answer()
    text, markup = _home()
    await edit_or_send(call, text, markup)  # type: ignore[arg-type]


@router.message(_is_archive)
async def on_archive(message: Message, bot: Bot, settings: Settings) -> None:
    document = message.document
    assert document is not None
    if not selfupdate.updater_installed():
        await message.answer(texts.NOT_INSTALLED)
        return
    module = selfupdate.updater_module()
    if document.file_size and document.file_size > module.MAX_ARCHIVE:
        await message.answer(texts.NOT_ARCHIVE)
        return
    target = selfupdate.archive_path(settings.state_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    note = await message.answer("⏳ Проверяю архив…")
    try:
        await bot.download(document, destination=target)
        info = await asyncio.to_thread(selfupdate.inspect_archive, target)
    except selfupdate.ArchiveError as exc:
        target.unlink(missing_ok=True)
        await note.edit_text(f"❌ {texts.esc(str(exc))}\n\n{texts.NOT_ARCHIVE}")
        return
    relation = selfupdate.compare_versions(info.version, __version__)
    can_install = not info.system_changed
    label = "✅ Установить" if relation > 0 else "⚠️ Всё равно установить"
    await note.edit_text(
        texts.render_archive(info, __version__),
        reply_markup=kb.update_confirm_kb(info.sig, int(time.time()), can_install=can_install, label=label),
    )


@router.callback_query(UpdateCb.filter(F.action == UpdateAction.NO))
async def cb_cancel(call: CallbackQuery, settings: Settings) -> None:
    selfupdate.archive_path(settings.state_dir).unlink(missing_ok=True)
    await call.answer("Отменено")
    await edit_or_send(call, "✖️ Обновление отменено. Архив удалён.")


async def _watchdog(bot: Bot, settings: Settings) -> None:
    """Если обновлятор за несколько минут не взял задачу — значит, не работает systemd-юнит; сообщаем."""
    await asyncio.sleep(WATCHDOG_SEC)
    if selfupdate.request_pending(settings.state_dir):
        await bot.send_message(settings.admin_id, texts.WATCHDOG)


def _spawn_watchdog(bot: Bot, settings: Settings) -> None:
    task = asyncio.create_task(_watchdog(bot, settings))
    _watchdogs.add(task)
    task.add_done_callback(_watchdogs.discard)


@router.callback_query(UpdateCb.filter(F.action == UpdateAction.GO))
async def _backup_before_update(settings: Settings, store: StateStore, db: Database) -> None:
    """💾 Копия базы и состояния перед обновлением; не получилось — обновление всё равно идёт (откат кода есть)."""
    try:
        await backup_svc.run_backup(settings.state_dir, db, store, keep=settings.backup_keep + 1, extra=[], tz=settings.tz)
    except backup_svc.BackupError as exc:
        log.warning("Backup before update failed: %s", exc)


async def cb_go(call: CallbackQuery, callback_data: UpdateCb, bot: Bot, settings: Settings, store: StateStore, db: Database) -> None:
    if not is_fresh(callback_data.ts, time.time()) and time.time() - callback_data.ts > selfupdate.CONFIRM_TTL_SEC:
        await call.answer(texts.STALE, show_alert=True)
        return
    path = selfupdate.archive_path(settings.state_dir)
    if not selfupdate.updater_installed():
        await call.answer("Обновлятор не установлен", show_alert=True)
        await edit_or_send(call, texts.NOT_INSTALLED)
        return
    try:
        info = await asyncio.to_thread(selfupdate.inspect_archive, path)
    except selfupdate.ArchiveError:
        await call.answer(texts.NOT_FOUND, show_alert=True)
        return
    if info.sig != callback_data.sig:
        await call.answer("Файл на сервере уже другой — пришлите архив заново", show_alert=True)
        return
    if info.system_changed:
        await call.answer("В этой версии изменились системные файлы — нужна обычная установка", show_alert=True)
        return
    if selfupdate.request_pending(settings.state_dir):
        await call.answer("Обновление уже запущено", show_alert=True)
        return
    await _backup_before_update(settings, store, db)
    selfupdate.write_request(settings.state_dir, "install", info)
    await call.answer("Запускаю")
    await edit_or_send(call, texts.render_started(info.version))
    _spawn_watchdog(bot, settings)


@router.callback_query(UpdateCb.filter(F.action == UpdateAction.ROLLBACK))
async def cb_rollback(call: CallbackQuery, callback_data: UpdateCb, bot: Bot, settings: Settings) -> None:
    previous = selfupdate.previous_version()
    if previous is None or not selfupdate.updater_installed():
        await call.answer("Прежней версии нет", show_alert=True)
        return
    if not callback_data.go:
        await call.answer()
        await edit_or_send(call, texts.render_rollback_confirm(__version__, previous), kb.rollback_confirm_kb(int(time.time())))
        return
    if not is_fresh(callback_data.ts, time.time()):
        await call.answer(texts.STALE, show_alert=True)
        return
    if selfupdate.request_pending(settings.state_dir):
        await call.answer("Обновление уже запущено", show_alert=True)
        return
    selfupdate.write_request(settings.state_dir, "rollback")
    await call.answer("Откатываю")
    await edit_or_send(call, f"⏳ <b>Возвращаю v{texts.esc(previous)}…</b>\nБот перезапустится и напишет о результате.")
    _spawn_watchdog(bot, settings)
