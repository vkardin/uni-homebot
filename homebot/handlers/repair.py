"""🛠 Ремонт сервера из чата: выбор действия → PIN (сообщение с ним удаляется) → результат и запись в журнал."""

from __future__ import annotations

from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from homebot.services.repair import Repair
from homebot.states import RepairStates
from homebot.ui import keyboards as kb
from homebot.ui.access_texts import esc
from homebot.ui.callbacks import RepairCb
from homebot.utils.telegram import edit_or_send

router = Router(name="repair")

OFF_TEXT = (
    "🛠 <b>Ремонт сервера</b> выключен.\nВключается только на самом сервере: в <code>.env</code> задайте "
    "<code>REPAIR_ENABLED=true</code> и <code>REPAIR_PIN=</code> (4–12 цифр), затем перезапустите бота."
)


def _menu_text(repair: Repair) -> str:
    lines = ["🛠 <b>Ремонт сервера</b>", "Выберите действие — спрошу PIN. Всё пишется в журнал.", ""]
    for item in repair.history(5):
        lines.append(f"{'✅' if item.get('ok') else '❌'} {esc(str(item.get('action')))} — {esc(str(item.get('detail', '')))}")
    return "\n".join(lines)


async def _show(target: Message | CallbackQuery, repair: Repair) -> None:
    if not repair.enabled:
        text, markup = OFF_TEXT, kb.repair_kb([])
    else:
        text, markup = _menu_text(repair), kb.repair_kb([(a.id, a.title) for a in repair.actions()])
    if isinstance(target, Message):
        await target.answer(text, reply_markup=markup)
    else:
        await edit_or_send(target, text, markup)


@router.message(Command("repair"))
async def cmd_repair(message: Message, repair: Repair, state: FSMContext) -> None:
    await state.clear()
    await _show(message, repair)


@router.callback_query(RepairCb.filter(F.act == ""))
async def cb_menu(call: CallbackQuery, repair: Repair, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await _show(call, repair)


@router.callback_query(RepairCb.filter(F.act != ""))
async def cb_action(call: CallbackQuery, callback_data: RepairCb, repair: Repair, state: FSMContext) -> None:
    action = next((a for a in repair.actions() if a.id == callback_data.act), None)
    if not repair.enabled or action is None:
        await call.answer("Недоступно", show_alert=True)
        return
    await call.answer()
    await state.set_state(RepairStates.pin)
    await state.update_data(act=action.id)
    note = "\n⚠️ Сервер уйдёт в перезагрузку, бот вернётся через пару минут." if action.danger else ""
    await edit_or_send(call, f"{action.title}\nПришлите PIN — сообщение с ним я сразу удалю.{note}", kb.repair_kb([]))


@router.message(RepairStates.pin, F.text, ~F.text.startswith("/"))
async def on_pin(message: Message, repair: Repair, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    with suppress(TelegramAPIError):
        await message.delete()
    user = message.from_user.id if message.from_user else 0
    ok, detail = await repair.run(str(data.get("act", "")), message.text or "", user)
    await message.answer(f"{'✅' if ok else '❌'} {esc(detail)}", reply_markup=kb.repair_kb([]))
