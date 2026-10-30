"""💱 P2P-курс Binance: текущая цена продажи, график, пороги уведомлений и целевая цена."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from homebot.config import Settings
from homebot.services.apps import Apps
from homebot.services.apps_http import AppError
from homebot.services.charts import render_p2p_chart
from homebot.services.p2p import HISTORY_KEY, STATE_KEY, P2PQuote, chart_points, parse_price, stats
from homebot.services.prefs import P2P_THRESHOLDS, Prefs, next_choice
from homebot.services.state import StateStore
from homebot.states import P2PStates
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, MenuCb, P2PAction, P2PCb
from homebot.ui.p2p_texts import OFF_TEXT, money, render_p2p
from homebot.utils.telegram import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="p2p")

_TARGET_PROMPT = (
    "🎯 <b>Целевая цена</b>\n"
    "Напишите цену продажи, при которой сообщить — например <code>41.5</code>.\n"
    "Скажу один раз, когда типичная цена дойдёт до неё, и снова — только после того, как она опустится ниже."
)
_BAD_PRICE = "Не разобрал число. Напишите цену цифрами, например <code>41.5</code>, или нажмите «Отмена»."


async def _view(apps: Apps, prefs: Prefs, settings: Settings, store: StateStore) -> tuple[str, InlineKeyboardMarkup]:
    service = apps.p2p
    assert service is not None
    quote: P2PQuote | None = None
    error = ""
    try:
        quote = await service.quote()
    except AppError as exc:
        error = str(exc)
    current = prefs.p2p()
    text = render_p2p(
        quote,
        error=error,
        day=stats(store.get(HISTORY_KEY), now=time.time()),
        prefs=current,
        asset=service.asset,
        fiat=service.fiat,
        amount=settings.p2p_amount,
        methods=settings.p2p_methods,
        tz=settings.tz,
    )
    return text, kb.p2p_kb(current)


async def _rearm(store: StateStore) -> None:
    """Цель сменили — сообщение о ней снова должно прийти (взводим и забываем «уже сообщали»)."""
    saved = store.get(STATE_KEY)
    state = dict(saved) if isinstance(saved, dict) else {}
    state["armed"] = True
    await store.set(STATE_KEY, state)


@router.message(Command("p2p"))
async def cmd_p2p(message: Message, state: FSMContext, apps: Apps, prefs: Prefs, settings: Settings, store: StateStore) -> None:
    await state.clear()
    if apps.p2p is None:
        await message.answer(OFF_TEXT)
        return
    placeholder = await message.answer("⏳ Смотрю объявления…")
    text, markup = await _view(apps, prefs, settings, store)
    await placeholder.edit_text(text, reply_markup=markup)


@router.callback_query(MenuCb.filter(F.action == MenuAction.P2P))
@router.callback_query(P2PCb.filter(F.action == P2PAction.HOME))
async def cb_home(call: CallbackQuery, state: FSMContext, apps: Apps, prefs: Prefs, settings: Settings, store: StateStore) -> None:
    await state.clear()
    if apps.p2p is None:
        await call.answer()
        await edit_or_send(call, OFF_TEXT, kb.back_kb())
        return
    await call.answer()
    text, markup = await _view(apps, prefs, settings, store)
    await edit_or_send(call, text, markup)


@router.callback_query(P2PCb.filter(F.action == P2PAction.THRESHOLD))
async def cb_threshold(call: CallbackQuery, apps: Apps, prefs: Prefs, settings: Settings, store: StateStore) -> None:
    if apps.p2p is None:
        await call.answer()
        return
    new = next_choice(P2P_THRESHOLDS, prefs.p2p().threshold)
    await prefs.update_p2p(threshold=new)
    await call.answer(f"Порог: ±{new:g}%" if new else "Уведомления об изменении выключены")
    text, markup = await _view(apps, prefs, settings, store)
    await edit_or_send(call, text, markup)


@router.callback_query(P2PCb.filter(F.action == P2PAction.SUMMARY))
async def cb_summary(call: CallbackQuery, apps: Apps, prefs: Prefs, settings: Settings, store: StateStore) -> None:
    if apps.p2p is None:
        await call.answer()
        return
    new = not prefs.p2p().summary
    await prefs.update_p2p(summary=new)
    await call.answer("Вечерняя сводка включена" if new else "Вечерняя сводка выключена")
    text, markup = await _view(apps, prefs, settings, store)
    await edit_or_send(call, text, markup)


@router.callback_query(P2PCb.filter(F.action == P2PAction.TARGET))
async def cb_target(call: CallbackQuery, state: FSMContext, apps: Apps) -> None:
    if apps.p2p is None:
        await call.answer()
        return
    await call.answer()
    await state.set_state(P2PStates.target)
    await edit_or_send(call, _TARGET_PROMPT, kb.p2p_cancel_kb())


@router.message(P2PStates.target, ~F.text.startswith("/"))
async def on_target(message: Message, state: FSMContext, apps: Apps, prefs: Prefs, settings: Settings, store: StateStore) -> None:
    if apps.p2p is None:
        await state.clear()
        await message.answer(OFF_TEXT)
        return
    price = parse_price(message.text or "")
    if price is None:
        await message.answer(_BAD_PRICE, reply_markup=kb.p2p_cancel_kb())
        return
    await prefs.update_p2p(target=price)
    await _rearm(store)
    await state.clear()
    await message.answer(f"🎯 Цель: {money(price, apps.p2p.fiat)}. Сообщу, когда цена до неё дойдёт.")
    text, markup = await _view(apps, prefs, settings, store)
    await message.answer(text, reply_markup=markup)


@router.callback_query(P2PCb.filter(F.action == P2PAction.TARGET_OFF))
async def cb_target_off(call: CallbackQuery, apps: Apps, prefs: Prefs, settings: Settings, store: StateStore) -> None:
    if apps.p2p is None:
        await call.answer()
        return
    await prefs.update_p2p(target=0.0)
    await _rearm(store)
    await call.answer("Цель убрана")
    text, markup = await _view(apps, prefs, settings, store)
    await edit_or_send(call, text, markup)


@router.callback_query(P2PCb.filter(F.action == P2PAction.CHART))
async def cb_chart(call: CallbackQuery, apps: Apps, settings: Settings, store: StateStore, bot: Bot) -> None:
    if apps.p2p is None or not isinstance(call.message, Message):
        await call.answer("P2P-курс выключен", show_alert=True)
        return
    raw = chart_points(store.get(HISTORY_KEY), now=time.time())
    if len(raw) < 2:
        await call.answer("Данных пока мало — график появится после пары проверок (раз в несколько минут).", show_alert=True)
        return
    await call.answer("📈 Рисую…")
    points = [(datetime.fromtimestamp(ts, settings.tz), best, typical) for ts, best, typical in raw]
    title = f"{apps.p2p.asset} → {apps.p2p.fiat} · Binance P2P · 7 дней"
    async with ChatActionSender.upload_photo(bot=bot, chat_id=call.message.chat.id):
        png = await asyncio.to_thread(render_p2p_chart, points, tz=settings.tz, title=title, fiat=apps.p2p.fiat)
    await call.message.answer_photo(BufferedInputFile(png, "p2p.png"), reply_markup=kb.p2p_alert_kb())
