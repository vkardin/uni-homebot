"""Графики метрик за 6 часов / сутки / неделю."""

from __future__ import annotations

import asyncio
import logging
import socket

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaPhoto, Message
from aiogram.utils.chat_action import ChatActionSender

from homebot.config import Settings
from homebot.services.charts import render_chart
from homebot.services.db import Database
from homebot.services.history import DEFAULT_PERIOD, PERIODS, load_period, summarize
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import ChartCb, MenuAction, MenuCb
from homebot.ui.texts import render_chart_caption
from homebot.utils.telegram import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="charts")


async def _build(db: Database, settings: Settings, period: str) -> tuple[bytes | None, str]:
    rows = await load_period(db, period)
    label = PERIODS[period][1]
    caption = render_chart_caption(summarize(rows), label)
    if not rows:
        return None, caption
    title = f"{socket.gethostname()} · {label}"
    try:
        png = await asyncio.to_thread(render_chart, rows, tz=settings.tz, title=title, long_period=period == "7d")
    except Exception:
        log.exception("Chart rendering failed")
        return None, caption + "\n⚠️ Не удалось нарисовать график (подробности в журнале)."
    return png, caption


async def _send_new(message: Message, bot: Bot, db: Database, settings: Settings, period: str) -> None:
    async with ChatActionSender.upload_photo(bot=bot, chat_id=message.chat.id):
        png, caption = await _build(db, settings, period)
    if png is None:
        await message.answer(caption, reply_markup=kb.charts_kb(period))
        return
    await message.answer_photo(BufferedInputFile(png, "chart.png"), caption=caption, reply_markup=kb.charts_kb(period))


@router.message(Command("charts", "graph"))
async def cmd_charts(message: Message, state: FSMContext, bot: Bot, db: Database, settings: Settings) -> None:
    await state.clear()
    await _send_new(message, bot, db, settings, DEFAULT_PERIOD)


@router.callback_query(MenuCb.filter(F.action == MenuAction.CHARTS))
async def cb_charts(call: CallbackQuery, state: FSMContext, bot: Bot, db: Database, settings: Settings) -> None:
    await call.answer("📈 Рисую…")
    await state.clear()
    if isinstance(call.message, Message):
        await _send_new(call.message, bot, db, settings, DEFAULT_PERIOD)


@router.callback_query(ChartCb.filter())
async def cb_period(call: CallbackQuery, callback_data: ChartCb, db: Database, settings: Settings) -> None:
    period = callback_data.period if callback_data.period in PERIODS else DEFAULT_PERIOD
    await call.answer("📈 Рисую…")
    message = call.message
    png, caption = await _build(db, settings, period)
    markup = kb.charts_kb(period)
    if not isinstance(message, Message):
        return
    if png is None:
        if message.photo:
            await message.edit_caption(caption=caption, reply_markup=markup)
        else:
            await edit_or_send(call, caption, markup)
        return
    media = InputMediaPhoto(media=BufferedInputFile(png, "chart.png"), caption=caption)
    if message.photo:
        await message.edit_media(media=media, reply_markup=markup)
    else:
        await message.answer_photo(BufferedInputFile(png, "chart.png"), caption=caption, reply_markup=markup)
