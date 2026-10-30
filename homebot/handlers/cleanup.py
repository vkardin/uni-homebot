"""🧹 Чистка диска: просмотренное в Jellyfin — удалить через Radarr/Sonarr (с подтверждением)."""

from __future__ import annotations

import contextlib
import logging
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from homebot.config import Settings
from homebot.services.apps import Apps
from homebot.services.apps_http import AppError
from homebot.services.cleanup import MAX_SHOWN, Watched, delete_watched, find_watched, forecast_full, signature
from homebot.services.db import Database
from homebot.services.power import is_fresh
from homebot.services.system import SystemService
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import CleanAction, CleanCb, MenuAction
from homebot.ui.extra_texts import (
    render_cleanup,
    render_cleanup_all_confirm,
    render_cleanup_confirm,
    render_cleanup_result,
)
from homebot.ui.texts import render_app_error
from homebot.utils.telegram import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="cleanup")

_TITLE = "🧹 <b>Чистка диска</b>"
_NEEDS = f"{_TITLE}\nНужны Jellyfin (чтобы знать, что просмотрено) и Radarr или Sonarr — см. .env."
_FORECAST_DAYS = 14


def _available(apps: Apps) -> bool:
    return apps.jellyfin is not None and (apps.radarr is not None or apps.sonarr is not None)


async def _items(apps: Apps, settings: Settings) -> list[Watched]:
    assert apps.jellyfin is not None
    return await find_watched(apps.jellyfin, apps.radarr, apps.sonarr, min_days=settings.cleanup_min_days)


async def _view(apps: Apps, system: SystemService, db: Database, settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    if not _available(apps):
        return _NEEDS, kb.back_kb(MenuAction.MOVIES, "🎬 Кино")
    try:
        items = await _items(apps, settings)
    except AppError as exc:
        return render_app_error(_TITLE, str(exc)), kb.back_kb(MenuAction.MOVIES, "🎬 Кино")
    snap = await system.snapshot()
    root = next((d for d in snap.disks if d.mountpoint == "/"), None)
    rows = await db.metrics_since(int(time.time()) - _FORECAST_DAYS * 86400)
    forecast = forecast_full([(r.ts, r.disk) for r in rows if r.disk is not None])
    text = render_cleanup(
        items,
        disk_percent=root.percent if root else None,
        disk_free=root.total - root.used if root else None,
        forecast=forecast,
        min_days=settings.cleanup_min_days,
    )
    return text, kb.cleanup_kb(items)


async def _refresh_library(apps: Apps) -> None:
    if apps.jellyfin is not None:
        with contextlib.suppress(AppError):  # не критично: Jellyfin и сам заметит исчезнувшие файлы
            await apps.jellyfin.refresh_library()


@router.message(Command("cleanup", "clean"))
async def cmd_cleanup(
    message: Message, state: FSMContext, apps: Apps, system: SystemService, db: Database, settings: Settings
) -> None:
    await state.clear()
    placeholder = await message.answer("⏳ Считаю…")
    text, markup = await _view(apps, system, db, settings)
    await placeholder.edit_text(text, reply_markup=markup)


@router.callback_query(CleanCb.filter(F.action == CleanAction.VIEW))
async def cb_cleanup(
    call: CallbackQuery, state: FSMContext, apps: Apps, system: SystemService, db: Database, settings: Settings
) -> None:
    await call.answer()
    await state.clear()
    text, markup = await _view(apps, system, db, settings)
    await edit_or_send(call, text, markup)


@router.callback_query(CleanCb.filter(F.action == CleanAction.DEL))
async def cb_delete(
    call: CallbackQuery, callback_data: CleanCb, apps: Apps, system: SystemService, db: Database, settings: Settings
) -> None:
    if not _available(apps):
        await call.answer("Нужны Jellyfin и Radarr/Sonarr", show_alert=True)
        return
    try:
        items = await _items(apps, settings)
    except AppError as exc:
        await call.answer(f"Не получилось: {exc}"[:200], show_alert=True)
        return
    item = next((i for i in items if i.kind == callback_data.kind and i.ref == callback_data.ref), None)
    if item is None:
        await call.answer("Этого уже нет в списке — обновляю")
        text, markup = await _view(apps, system, db, settings)
        await edit_or_send(call, text, markup)
        return
    if not callback_data.go:
        await call.answer()
        await edit_or_send(call, render_cleanup_confirm(item), kb.cleanup_confirm_kb(item.kind, item.ref, int(time.time())))
        return
    if not is_fresh(callback_data.ts, time.time()):
        await call.answer("Подтверждение устарело — повторите", show_alert=True)
        return
    await call.answer("Удаляю…")
    removed, freed, errors = await delete_watched(apps.radarr, apps.sonarr, [item])
    await _refresh_library(apps)
    text, markup = await _view(apps, system, db, settings)
    await edit_or_send(call, f"{render_cleanup_result(removed, freed, errors)}\n\n{text}", markup)


@router.callback_query(CleanCb.filter(F.action == CleanAction.ALL))
async def cb_delete_all(
    call: CallbackQuery, callback_data: CleanCb, apps: Apps, system: SystemService, db: Database, settings: Settings
) -> None:
    if not _available(apps):
        await call.answer("Нужны Jellyfin и Radarr/Sonarr", show_alert=True)
        return
    try:
        items = (await _items(apps, settings))[:MAX_SHOWN]
    except AppError as exc:
        await call.answer(f"Не получилось: {exc}"[:200], show_alert=True)
        return
    if not items or len(items) != callback_data.ref or signature(items) != callback_data.kind:
        # список поменялся, пока смотрели на кнопку, — удалять вслепую нельзя
        await call.answer("Список изменился — смотрите актуальный")
        text, markup = await _view(apps, system, db, settings)
        await edit_or_send(call, text, markup)
        return
    if not callback_data.go:
        await call.answer()
        await edit_or_send(
            call, render_cleanup_all_confirm(items), kb.cleanup_all_confirm_kb(len(items), signature(items), int(time.time()))
        )
        return
    if not is_fresh(callback_data.ts, time.time()):
        await call.answer("Подтверждение устарело — повторите", show_alert=True)
        return
    await call.answer("Удаляю…")
    removed, freed, errors = await delete_watched(apps.radarr, apps.sonarr, items)
    await _refresh_library(apps)
    text, markup = await _view(apps, system, db, settings)
    await edit_or_send(call, f"{render_cleanup_result(removed, freed, errors)}\n\n{text}", markup)
