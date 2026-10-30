"""«Кинул и забыл»: magnet → qBittorrent, ссылки на товары → слежка, голосовое → заметка / напоминание / расход."""

from __future__ import annotations

import logging
import tempfile
import time
from datetime import datetime
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from homebot.config import Settings
from homebot.handlers.finance import _start_draft
from homebot.services import voice
from homebot.services.apps import Apps
from homebot.services.apps_http import AppError
from homebot.services.db import Database
from homebot.services.expense_parse import parse_expense
from homebot.services.quickadd import MAGNET_RE, ONLY_URLS_RE, magnet_name
from homebot.services.reminders import parse_recurring, parse_reminder
from homebot.services.sites import extract_urls
from homebot.states import SitesStates
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, VoiceAction, VoiceCb
from homebot.ui.sites_texts import render_mode_prompt
from homebot.utils.telegram import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="quick")

_drafts = voice.Drafts()


# --------------------------------------------------------------------- magnet
def _has_magnet(message: Message) -> bool:
    return bool(message.text and MAGNET_RE.search(message.text))


@router.message(StateFilter(None), F.text, _has_magnet)
async def on_magnet(message: Message, apps: Apps) -> None:
    match = MAGNET_RE.search(message.text or "")
    assert match is not None
    if apps.qbit is None:
        await message.answer("🧲 qBittorrent не подключён — добавить раздачу не могу.")
        return
    magnet = match.group(0)
    try:
        await apps.qbit.add_magnet(magnet)
    except AppError as exc:
        await message.answer(f"🧲 Не получилось: {exc}")
        return
    await message.answer(
        f"🧲 Добавил в qBittorrent: <b>{_esc(magnet_name(magnet))}</b>", reply_markup=kb.back_kb(MenuAction.APPS, "🐳 Приложения")
    )


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- ссылки → слежка
def _only_urls(message: Message) -> bool:
    return bool(message.text and ONLY_URLS_RE.match(message.text))


@router.message(StateFilter(None), F.text, _only_urls)
async def on_links(message: Message, state: FSMContext, apps: Apps) -> None:
    urls = extract_urls(message.text or "")
    if apps.sites is None or not urls:
        await message.answer("🤔 Я не понял. Откройте меню: /menu")
        return
    await state.set_state(SitesStates.url)
    await state.update_data(urls=urls)
    await message.answer(render_mode_prompt(urls), reply_markup=kb.sites_mode_kb())


# -------------------------------------------------------------------- голосовые
@router.message(StateFilter(None), F.voice)
async def on_voice(message: Message, bot: Bot, settings: Settings, apps: Apps) -> None:
    voice_msg = message.voice
    assert voice_msg is not None
    if voice_msg.duration > settings.voice_max_sec:
        await message.answer(f"🎙 Длинновато: максимум {settings.voice_max_sec} с.")
        return
    if not voice.available():
        await message.answer("🎙 " + voice.INSTALL_HINT)
        return
    status = await message.answer("🎙 Слушаю…")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "voice.ogg"
            await bot.download(voice_msg, destination=path)
            text = await voice.transcribe(
                path, model=settings.voice_model, language=settings.voice_language, root=settings.state_dir / "models"
            )
    except voice.VoiceUnavailable as exc:
        await status.edit_text("🎙 " + str(exc))
        return
    except (voice.VoiceError, OSError) as exc:
        await status.edit_text(f"🎙 Не получилось: {exc}")
        return
    if not text:
        await status.edit_text("🎙 Ничего не разобрал — попробуйте сказать чётче или ближе к микрофону.")
        return
    now = datetime.now(settings.tz)
    can_remind = bool(parse_recurring(text, now) or parse_reminder(text, now))
    can_spend = apps.actual is not None and parse_expense(text) is not None
    ref = _drafts.put(text, int(time.time()) % 100_000)
    await status.edit_text(
        f"🎙 <b>Услышал:</b>\n{_esc(text)}\n\nЧто с этим сделать?", reply_markup=kb.voice_kb(ref, remind=can_remind, spend=can_spend)
    )


@router.callback_query(VoiceCb.filter())
async def cb_voice(call: CallbackQuery, callback_data: VoiceCb, db: Database, settings: Settings, apps: Apps, store: object) -> None:
    text = _drafts.get(callback_data.ref)
    if text is None:
        await call.answer("Это голосовое уже обработано или устарело", show_alert=True)
        return
    action = callback_data.action
    if action == VoiceAction.DROP:
        _drafts.drop(callback_data.ref)
        await call.answer("Ок")
        await edit_or_send(call, "🎙 Оставил как есть.")
        return
    if action == VoiceAction.NOTE:
        await db.add_note(int(time.time()), text[:2000])
        _drafts.drop(callback_data.ref)
        await call.answer("Сохранено")
        await edit_or_send(call, "📝 Заметка сохранена", kb.back_kb(MenuAction.NOTES, "📝 Все заметки"))
        return
    if action == VoiceAction.REMIND:
        now = datetime.now(settings.tz)
        parsed = parse_recurring(text, now) or parse_reminder(text, now)
        if parsed is None:
            await call.answer("Не понял время", show_alert=True)
            return
        await db.add_reminder(int(parsed.due.timestamp()), parsed.text, parsed.rule)
        _drafts.drop(callback_data.ref)
        await call.answer("Готово")
        await edit_or_send(
            call, f"⏰ Напомню {parsed.due:%d.%m в %H:%M}: {_esc(parsed.text)}", kb.back_kb(MenuAction.REMINDERS, "⏰ Все напоминания")
        )
        return
    if action == VoiceAction.SPEND and apps.actual is not None and isinstance(call.message, Message):
        _drafts.drop(callback_data.ref)
        await call.answer()
        await _start_draft(call.message, text, apps.actual, settings, store, mode=None)
        return
    await call.answer("Недоступно", show_alert=True)
