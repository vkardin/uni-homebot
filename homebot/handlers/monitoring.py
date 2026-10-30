"""Мониторинг: состояние сервера, топ процессов, статус служб."""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from homebot.services.system import SystemService
from homebot.services.units import UnitsService
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, MenuCb
from homebot.ui.texts import render_live, render_services, render_status, render_top
from homebot.utils.telegram import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="monitoring")

LIVE_INTERVAL = 5
LIVE_DURATION = 180
_live_tasks: dict[int, asyncio.Task[None]] = {}  # chat_id -> задача обновления


async def _live_loop(message: Message, system: SystemService) -> None:
    """Обновляет одно сообщение раз в LIVE_INTERVAL секунд, пока не нажмут «Остановить» или не выйдет время."""
    deadline = time.monotonic() + LIVE_DURATION
    try:
        while (remaining := int(deadline - time.monotonic())) > 0:
            snap = await system.snapshot()
            try:
                await message.edit_text(render_live(snap, interval=LIVE_INTERVAL, remaining=remaining), reply_markup=kb.live_kb())
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)
                continue
            except TelegramBadRequest as exc:
                if "not modified" not in str(exc).lower():
                    return  # сообщение удалили или оно устарело
            await asyncio.sleep(LIVE_INTERVAL)
        await message.edit_text(render_status(await system.snapshot()), reply_markup=kb.status_kb())
    except asyncio.CancelledError:
        raise
    except TelegramAPIError as exc:
        log.debug("Live panel stopped: %s", exc)
    except Exception:
        log.exception("Live panel crashed")
    finally:
        if _live_tasks.get(message.chat.id) is asyncio.current_task():
            del _live_tasks[message.chat.id]


async def _stop_live(chat_id: int) -> bool:
    task = _live_tasks.pop(chat_id, None)
    if task is None or task.done():
        return False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return True


async def _status_view(system: SystemService) -> tuple[str, InlineKeyboardMarkup]:
    return render_status(await system.snapshot()), kb.status_kb()


async def _top_view(system: SystemService) -> tuple[str, InlineKeyboardMarkup]:
    return render_top(await system.top_processes(limit=5)), kb.top_kb()


async def _services_view(units: UnitsService) -> tuple[str, InlineKeyboardMarkup]:
    return render_services(await units.status_all()), kb.services_kb()


# ------------------------------------------------------------------- команды
@router.message(Command("status"))
async def cmd_status(message: Message, state: FSMContext, system: SystemService) -> None:
    await state.clear()
    text, markup = await _status_view(system)
    await message.answer(text, reply_markup=markup)


@router.message(Command("top"))
async def cmd_top(message: Message, state: FSMContext, system: SystemService) -> None:
    await state.clear()
    text, markup = await _top_view(system)
    await message.answer(text, reply_markup=markup)


@router.message(Command("services"))
async def cmd_services(message: Message, state: FSMContext, units: UnitsService) -> None:
    await state.clear()
    text, markup = await _services_view(units)
    await message.answer(text, reply_markup=markup)


# -------------------------------------------------------------------- кнопки
@router.callback_query(MenuCb.filter(F.action == MenuAction.STATUS))
async def cb_status(call: CallbackQuery, state: FSMContext, system: SystemService) -> None:
    await call.answer()
    await state.clear()
    text, markup = await _status_view(system)
    await edit_or_send(call, text, markup)


@router.callback_query(MenuCb.filter(F.action == MenuAction.LIVE))
async def cb_live(call: CallbackQuery, state: FSMContext, system: SystemService) -> None:
    message = call.message
    if not isinstance(message, Message):
        await call.answer("Сообщение устарело — откройте /status", show_alert=True)
        return
    await call.answer("📡 Live включён на 3 минуты")
    await state.clear()
    await _stop_live(message.chat.id)
    _live_tasks[message.chat.id] = asyncio.create_task(_live_loop(message, system), name="live-panel")


@router.callback_query(MenuCb.filter(F.action == MenuAction.LIVE_STOP))
async def cb_live_stop(call: CallbackQuery, system: SystemService) -> None:
    if isinstance(call.message, Message):
        await _stop_live(call.message.chat.id)
    await call.answer("Остановлено")
    text, markup = await _status_view(system)
    await edit_or_send(call, text, markup)


@router.callback_query(MenuCb.filter(F.action == MenuAction.TOP))
async def cb_top(call: CallbackQuery, state: FSMContext, system: SystemService) -> None:
    await call.answer()
    await state.clear()
    text, markup = await _top_view(system)
    await edit_or_send(call, text, markup)


@router.callback_query(MenuCb.filter(F.action == MenuAction.SERVICES))
async def cb_services(call: CallbackQuery, state: FSMContext, units: UnitsService) -> None:
    await call.answer()
    await state.clear()
    text, markup = await _services_view(units)
    await edit_or_send(call, text, markup)


async def stop_all_live() -> None:
    tasks = [t for t in _live_tasks.values() if not t.done()]
    _live_tasks.clear()
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
