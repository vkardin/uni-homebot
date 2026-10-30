"""⚙️ Настройки прямо в боте: новости, источники (ссылки и готовые подборки)."""

from __future__ import annotations

import asyncio
import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from homebot.config import Settings
from homebot.services.news import NewsService, feed_name
from homebot.services.ollama import OllamaError, OllamaService
from homebot.services.prefs import COUNT_CHOICES, INTERVAL_CHOICES, MODE_DAY, MODE_NEW, QUIET_CHOICES, Prefs, next_choice
from homebot.services.presets import PRESET_KEYS, PRESETS
from homebot.services.webtext import extract_urls
from homebot.states import SettingsStates
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, MenuCb, SetAction, SetCb, SetSection
from homebot.ui.extra_texts import (
    render_night,
    render_translate_models,
)
from homebot.ui.texts import (
    ADD_FEED_TEXT,
    SETTINGS_TEXT,
    render_feeds,
    render_news_models,
    render_news_settings,
    render_ollama_error,
    render_presets,
)
from homebot.utils.telegram import edit_or_send

router = Router(name="settings")
MAX_FEEDS = 30


# ------------------------------------------------------------------ корень
@router.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(SETTINGS_TEXT, reply_markup=kb.settings_kb())


@router.callback_query(MenuCb.filter(F.action == MenuAction.SETTINGS))
@router.callback_query(SetCb.filter(F.sec == SetSection.ROOT))
async def cb_settings(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await edit_or_send(call, SETTINGS_TEXT, kb.settings_kb())


# ------------------------------------------------------------------ новости
@router.callback_query(SetCb.filter(F.sec == SetSection.NEWS))
async def cb_news_settings(call: CallbackQuery, callback_data: SetCb, state: FSMContext, prefs: Prefs) -> None:
    await state.clear()
    p = prefs.news()
    act = callback_data.act
    if act == SetAction.ENABLED:
        p = await prefs.update_news(enabled=not p.enabled)
    elif act == SetAction.INTERVAL:
        p = await prefs.update_news(interval_min=next_choice(INTERVAL_CHOICES, p.interval_min))
    elif act == SetAction.COUNT:
        p = await prefs.update_news(count=next_choice(COUNT_CHOICES, p.count))
    elif act == SetAction.QUIET:
        p = await prefs.update_news(quiet=not p.quiet)
    elif act == SetAction.QUIET_RANGE:
        start, end = next_choice(QUIET_CHOICES, (p.quiet_from, p.quiet_to))
        p = await prefs.update_news(quiet_from=start, quiet_to=end, quiet=True)
    elif act == SetAction.RETELL:
        p = await prefs.update_news(retell=not p.retell)
    elif act == SetAction.ARTICLES:
        p = await prefs.update_news(fetch_articles=not p.fetch_articles)
    elif act == SetAction.MODE:
        p = await prefs.update_news(mode=MODE_NEW if p.mode == MODE_DAY else MODE_DAY)
    await call.answer("Сохранено" if act != SetAction.VIEW else None)
    await edit_or_send(call, render_news_settings(p), kb.news_settings_kb(p))


# ---------------------------------------------------------------- источники
@router.callback_query(SetCb.filter((F.sec == SetSection.FEEDS) & (F.act == SetAction.VIEW)))
async def cb_feeds(call: CallbackQuery, state: FSMContext, prefs: Prefs) -> None:
    await call.answer()
    await state.clear()
    feeds = prefs.news().feeds
    await edit_or_send(call, render_feeds(feeds), kb.feeds_kb(feeds))


@router.callback_query(SetCb.filter((F.sec == SetSection.FEEDS) & (F.act == SetAction.DELETE)))
async def cb_feed_delete(call: CallbackQuery, callback_data: SetCb, prefs: Prefs) -> None:
    feeds = list(prefs.news().feeds)
    if not 0 <= callback_data.idx < len(feeds):
        await call.answer("Список изменился — обновляю")
    else:
        removed = feeds.pop(callback_data.idx)
        await prefs.update_news(feeds=tuple(feeds))
        await call.answer(f"Удалено: {feed_name(removed)}")
    feeds = list(prefs.news().feeds)
    await edit_or_send(call, render_feeds(feeds), kb.feeds_kb(feeds))


@router.callback_query(SetCb.filter((F.sec == SetSection.FEEDS) & (F.act == SetAction.ADD)))
async def cb_feed_add(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(SettingsStates.add_feed)
    await edit_or_send(call, ADD_FEED_TEXT, kb.cancel_kb(SetCb(sec=SetSection.FEEDS)))


@router.message(SettingsStates.add_feed, ~F.text.startswith("/"))
async def on_feed_url(message: Message, state: FSMContext, prefs: Prefs, news: NewsService) -> None:
    urls = extract_urls(message.text or "")
    if not urls:
        await message.answer("Не вижу ссылки. " + ADD_FEED_TEXT, reply_markup=kb.cancel_kb(SetCb(sec=SetSection.FEEDS)))
        return
    url = urls[0]
    feeds = list(prefs.news().feeds)
    if url in feeds:
        await state.clear()
        await message.answer("Эта лента уже есть.", reply_markup=kb.feeds_kb(feeds))
        return
    if len(feeds) >= MAX_FEEDS:
        await state.clear()
        await message.answer(f"Лимит — {MAX_FEEDS} источников. Удалите лишние.", reply_markup=kb.feeds_kb(feeds))
        return
    checking = await message.answer("⏳ Проверяю ленту…")
    ok, info = await news.check_feed(url)
    if not ok:
        await checking.edit_text(
            f"❌ Не получилось: {html.escape(info)}\nПришлите другую ссылку или нажмите «Отмена».",
            reply_markup=kb.cancel_kb(SetCb(sec=SetSection.FEEDS)),
        )
        return
    await state.clear()
    updated = await prefs.update_news(feeds=(*feeds, url))
    await checking.edit_text(
        f"✅ Добавлено: <b>{html.escape(info)}</b>\n\n{render_feeds(updated.feeds)}",
        reply_markup=kb.feeds_kb(updated.feeds),
    )


# ------------------------------------------------------------------ подборки
@router.callback_query(SetCb.filter((F.sec == SetSection.PRESETS) & (F.act == SetAction.VIEW)))
async def cb_presets(call: CallbackQuery, prefs: Prefs) -> None:
    await call.answer()
    await edit_or_send(call, render_presets(), kb.presets_kb(set(prefs.news().feeds)))


@router.callback_query(SetCb.filter((F.sec == SetSection.PRESETS) & (F.act == SetAction.PRESET)))
async def cb_preset_add(call: CallbackQuery, callback_data: SetCb, prefs: Prefs, news: NewsService) -> None:
    if not 0 <= callback_data.idx < len(PRESET_KEYS):
        await call.answer("Подборка не найдена", show_alert=True)
        return
    title, urls = PRESETS[PRESET_KEYS[callback_data.idx]]
    current = list(prefs.news().feeds)
    todo = [u for u in urls if u not in current][: max(0, MAX_FEEDS - len(current))]
    if not todo:
        await call.answer("Эта подборка уже добавлена")
        return
    await call.answer("⏳ Проверяю ленты…")
    await edit_or_send(call, f"⏳ Проверяю ленты подборки «{html.escape(title)}»…")
    results = await asyncio.gather(*(news.check_feed(u) for u in todo))
    added = [u for u, (ok, _) in zip(todo, results, strict=True) if ok]
    failed = [f"{feed_name(u)} ({info})" for u, (ok, info) in zip(todo, results, strict=True) if not ok]
    if added:
        await prefs.update_news(feeds=(*prefs.news().feeds, *[u for u in added if u not in prefs.news().feeds]))
    lines = [f"📦 <b>{html.escape(title)}</b>"]
    if added:
        lines.append("✅ Добавлено: " + ", ".join(html.escape(feed_name(u)) for u in added))
    if failed:
        lines.append("❌ Не открылись: " + html.escape("; ".join(failed)))
    lines += ["", render_presets()]
    await edit_or_send(call, "\n".join(lines), kb.presets_kb(set(prefs.news().feeds)))


# ------------------------------------------------------------ модель для новостей
@router.callback_query(SetCb.filter(F.sec == SetSection.MODEL))
async def cb_news_model(call: CallbackQuery, callback_data: SetCb, prefs: Prefs, ollama: OllamaService) -> None:
    try:
        models = await ollama.list_models()
    except OllamaError as exc:
        await call.answer()
        await edit_or_send(call, render_ollama_error(exc), kb.news_settings_kb(prefs.news()))
        return
    current = prefs.news().model
    if callback_data.act == SetAction.MODEL:
        if callback_data.idx == -1:
            current = ""
        elif 0 <= callback_data.idx < len(models):
            current = models[callback_data.idx]
        else:
            await call.answer("Список моделей изменился — выберите снова")
            await edit_or_send(call, render_news_models(current, ollama.active_model), kb.news_models_kb(models, current))
            return
        await prefs.update_news(model=current)
        await call.answer("Сохранено")
        await edit_or_send(call, render_news_settings(prefs.news()), kb.news_settings_kb(prefs.news()))
        return
    await call.answer()
    await edit_or_send(call, render_news_models(current, ollama.active_model), kb.news_models_kb(models, current))


# ------------------------------------------------------------------ ночной режим
@router.callback_query(SetCb.filter(F.sec == SetSection.NIGHT))
async def cb_night(call: CallbackQuery, callback_data: SetCb, state: FSMContext, prefs: Prefs) -> None:
    await state.clear()
    night = prefs.night()
    if callback_data.act == SetAction.ENABLED:
        night = await prefs.update_night(enabled=not night.enabled)
    elif callback_data.act == SetAction.QUIET_RANGE:
        start, end = next_choice(QUIET_CHOICES, (night.start, night.end))
        night = await prefs.update_night(start=start, end=end)
    await call.answer()
    await edit_or_send(call, render_night(night), kb.night_kb(night))


# ------------------------------------------------------------ модель перевода
@router.callback_query(SetCb.filter(F.sec == SetSection.TRANSLATE))
async def cb_translate_model(
    call: CallbackQuery, callback_data: SetCb, prefs: Prefs, ollama: OllamaService, settings: Settings
) -> None:
    try:
        models = await ollama.list_models()
    except OllamaError as exc:
        await call.answer()
        await edit_or_send(call, render_ollama_error(exc), kb.settings_kb())
        return
    current = prefs.translate_model()
    if callback_data.act == SetAction.MODEL:
        if callback_data.idx == -1:
            current = ""
        elif 0 <= callback_data.idx < len(models):
            current = models[callback_data.idx]
        else:
            await call.answer("Список моделей изменился — выберите снова")
            current = prefs.translate_model()
        await prefs.set_translate_model(current)
        await call.answer("Сохранено — действует сразу")
    else:
        await call.answer()
    text = render_translate_models(current, ollama.active_model, machine=bool(settings.libretranslate_url))
    await edit_or_send(call, text, kb.translate_models_kb(models, current))
