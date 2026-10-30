"""Новости и сводка: экран раздела, «⚡ Обновить сейчас», «📰 Сводка дня»."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from homebot.config import Settings
from homebot.jobs.hourly_news import next_slot
from homebot.services.brief import KEY_LAST_RUN, BriefService
from homebot.services.db import Database
from homebot.services.prefs import Prefs
from homebot.services.state import StateStore
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, MenuCb, NewsAction, NewsCb
from homebot.ui.texts import render_brief, render_news_screen
from homebot.utils.telegram import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="news")


async def _screen(prefs: Prefs, db: Database, store: StateStore, settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    last = store.get(KEY_LAST_RUN)
    last_run = datetime.fromtimestamp(last, UTC) if isinstance(last, int | float) else None
    recent = await db.recent_sent_news(limit=8)
    return render_news_screen(prefs.news(), recent, last_run, settings.tz), kb.news_kb()


@router.message(Command("news"))
async def cmd_news(message: Message, state: FSMContext, prefs: Prefs, db: Database, store: StateStore, settings: Settings) -> None:
    await state.clear()
    text, markup = await _screen(prefs, db, store, settings)
    await message.answer(text, reply_markup=markup)


@router.callback_query(MenuCb.filter(F.action == MenuAction.NEWS))
async def cb_news(call: CallbackQuery, state: FSMContext, prefs: Prefs, db: Database, store: StateStore, settings: Settings) -> None:
    await call.answer()
    await state.clear()
    text, markup = await _screen(prefs, db, store, settings)
    await edit_or_send(call, text, markup)


@router.callback_query(NewsCb.filter(F.action == NewsAction.NOW))
async def cb_brief_now(call: CallbackQuery, briefs: BriefService, prefs: Prefs, settings: Settings, bot: Bot) -> None:
    """Собрать свежие новости и превратить ЭТО сообщение в живую сводку дня (дальше его обновляет расписание)."""
    message = call.message
    if not isinstance(message, Message):
        await call.answer("Сообщение устарело — откройте /news", show_alert=True)
        return
    await call.answer("⏳ Собираю…")
    await edit_or_send(call, "🤔 <b>UNI читает ленты…</b>\nОтберу свежее и перескажу коротко. Обычно 1–3 минуты.")
    current = prefs.news()
    now = datetime.now(UTC)
    brief = await briefs.build(now, next_at=next_slot(now, current.interval_min, settings.tz) if current.enabled else None)
    await briefs.deliver(
        bot,
        message.chat.id,
        render_brief(brief, settings.tz),
        kb.brief_kb(),
        brief,
        prefs=current,
        reuse_message_id=message.message_id,
    )
