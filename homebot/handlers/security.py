"""Безопасность: SSH-входы, неудачные попытки, обновления, диски."""

from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from homebot.config import Settings
from homebot.services.smart import SmartService
from homebot.services.ssh_log import SshLog
from homebot.services.updates import UpdatesService
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, MenuCb
from homebot.ui.texts import render_security
from homebot.utils.telegram import edit_or_send

router = Router(name="security")
_PLACEHOLDER = "⏳ Проверяю журналы, обновления и диски…"


async def _view(ssh_log: SshLog, updates: UpdatesService, smart: SmartService, settings: Settings) -> str:
    (logins, logins_error), (failed, failed_error), info = await asyncio.gather(
        ssh_log.recent_logins(), ssh_log.failed_count(), updates.check()
    )
    return render_security(
        logins=logins,
        logins_error=logins_error,
        failed=failed,
        failed_error=failed_error,
        updates=info,
        smart=smart.read(),
        tz=settings.tz,
    )


@router.message(Command("security"))
async def cmd_security(
    message: Message,
    state: FSMContext,
    ssh_log: SshLog,
    updates: UpdatesService,
    smart: SmartService,
    settings: Settings,
) -> None:
    await state.clear()
    placeholder = await message.answer(_PLACEHOLDER)
    await placeholder.edit_text(await _view(ssh_log, updates, smart, settings), reply_markup=kb.refresh_kb(MenuAction.SECURITY))


@router.callback_query(MenuCb.filter(F.action == MenuAction.SECURITY))
async def cb_security(
    call: CallbackQuery,
    state: FSMContext,
    ssh_log: SshLog,
    updates: UpdatesService,
    smart: SmartService,
    settings: Settings,
) -> None:
    await call.answer()
    await state.clear()
    await edit_or_send(call, _PLACEHOLDER)
    await edit_or_send(call, await _view(ssh_log, updates, smart, settings), kb.refresh_kb(MenuAction.SECURITY))
