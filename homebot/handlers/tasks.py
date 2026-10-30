"""Управление фоновыми задачами (алерты, новости, P2P, проверки)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from homebot.services.tasks import TaskManager
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, MenuCb, TaskCb
from homebot.ui.texts import render_tasks
from homebot.utils.telegram import edit_or_send

router = Router(name="tasks")


@router.message(Command("tasks"))
async def cmd_tasks(message: Message, state: FSMContext, tasks: TaskManager) -> None:
    await state.clear()
    items = tasks.items()
    await message.answer(render_tasks(items), reply_markup=kb.tasks_kb(items))


@router.callback_query(MenuCb.filter(F.action == MenuAction.TASKS))
async def cb_tasks(call: CallbackQuery, state: FSMContext, tasks: TaskManager) -> None:
    await call.answer()
    await state.clear()
    items = tasks.items()
    await edit_or_send(call, render_tasks(items), kb.tasks_kb(items))


@router.callback_query(TaskCb.filter())
async def cb_toggle(call: CallbackQuery, callback_data: TaskCb, tasks: TaskManager) -> None:
    try:
        running = await tasks.toggle(callback_data.name)
    except KeyError:
        await call.answer("Неизвестная задача", show_alert=True)
        return
    await call.answer("Включено" if running else "Выключено")
    items = tasks.items()
    await edit_or_send(call, render_tasks(items), kb.tasks_kb(items))
