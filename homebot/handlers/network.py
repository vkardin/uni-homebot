"""Сеть: Tailscale, внешний IP, скорость."""

from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from homebot.config import Settings
from homebot.services.apps import Apps
from homebot.services.apps_http import AppError
from homebot.services.network import NetworkService, TailnetStatus, TailscaleError
from homebot.services.speedtest import SpeedResult
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, MenuCb
from homebot.ui.texts import render_network
from homebot.utils.telegram import edit_or_send

router = Router(name="network")
_PLACEHOLDER = "⏳ Опрашиваю сеть…"


async def _view(network: NetworkService, apps: Apps, settings: Settings) -> str:
    async def tailnet() -> tuple[TailnetStatus | None, str | None]:
        try:
            return await network.tailscale(), None
        except TailscaleError as exc:
            return None, str(exc)

    async def speed() -> tuple[SpeedResult | None, str | None]:
        if apps.speedtest is None:
            return None, "не подключён (SPEEDTEST_TOKEN в .env)"
        try:
            result = await apps.speedtest.latest()
        except AppError as exc:
            return None, str(exc)
        return result, None if result is not None else "замеров ещё не было"

    (status, error), ip, (rx, tx, total_rx, total_tx), (speed_result, speed_note) = await asyncio.gather(
        tailnet(), network.external_ip(), network.throughput(), speed()
    )
    return render_network(
        tailnet=status,
        tailnet_error=error,
        external_ip=ip,
        rx=rx,
        tx=tx,
        total_rx=total_rx,
        total_tx=total_tx,
        speed=speed_result,
        speed_note=speed_note,
        tz=settings.tz,
    )


@router.message(Command("net", "network"))
async def cmd_network(message: Message, state: FSMContext, network: NetworkService, apps: Apps, settings: Settings) -> None:
    await state.clear()
    placeholder = await message.answer(_PLACEHOLDER)
    await placeholder.edit_text(await _view(network, apps, settings), reply_markup=kb.refresh_kb(MenuAction.NETWORK))


@router.callback_query(MenuCb.filter(F.action == MenuAction.NETWORK))
async def cb_network(call: CallbackQuery, state: FSMContext, network: NetworkService, apps: Apps, settings: Settings) -> None:
    await call.answer()
    await state.clear()
    await edit_or_send(call, _PLACEHOLDER)
    await edit_or_send(call, await _view(network, apps, settings), kb.refresh_kb(MenuAction.NETWORK))
