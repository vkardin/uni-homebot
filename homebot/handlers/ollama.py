"""Работа с локальной Ollama: панель, чат (FSM), резюме текста, выбор модели, выгрузка из RAM."""

from __future__ import annotations

import asyncio
import html
import io
import logging
from contextlib import aclosing

import aiohttp
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from homebot.services.ollama import OllamaError, OllamaService, strip_think, visible_text
from homebot.services.webtext import MAX_DOWNLOAD, WebTextError, document_to_text, extract_urls, fetch_text
from homebot.states import OllamaStates
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, MenuCb, OllamaAction, OllamaCb
from homebot.ui.texts import (
    CHAT_INTRO,
    SUMMARIZE_INTRO,
    render_ollama_error,
    render_ollama_panel,
)
from homebot.utils.formatting import split_message
from homebot.utils.telegram import UNI_THINK, edit_or_send, send_picture

log = logging.getLogger(__name__)
router = Router(name="ollama")

_EMPTY_HINT = "Пришлите текстовое сообщение."
_STREAM_EDIT_EVERY = 1.5  # чаще Telegram начинает ограничивать редактирование
_STREAM_VIEW_LIMIT = 3900


async def _safe_edit(message: Message, text: str, markup: InlineKeyboardMarkup | None = None, *, final: bool = False) -> None:
    """Редактирование для потокового вывода: промежуточные сбои не важны, финальный — повторяем."""
    for _ in range(3 if final else 1):
        try:
            await message.edit_text(text, parse_mode=None, reply_markup=markup)
            return
        except TelegramRetryAfter as exc:
            if not final:
                return
            await asyncio.sleep(exc.retry_after)
        except TelegramBadRequest as exc:
            if "not modified" not in str(exc).lower():
                log.debug("Stream edit failed: %s", exc)
            return


async def _stream_reply(message: Message, ollama: OllamaService, history: list[dict[str, str]]) -> str | None:
    """Показывает ответ по мере генерации. Возвращает итоговый текст или None при полной неудаче."""
    placeholder = await message.answer("💭 …", parse_mode=None)
    loop = asyncio.get_running_loop()
    buffer = ""
    last_view = "💭 …"
    next_edit = loop.time() + _STREAM_EDIT_EVERY
    interrupted = False
    try:
        async with aclosing(ollama.chat_stream(history)) as stream:
            async for piece in stream:
                buffer += piece
                if loop.time() >= next_edit:
                    next_edit = loop.time() + _STREAM_EDIT_EVERY
                    shown, thinking = visible_text(buffer)
                    view = (shown[:_STREAM_VIEW_LIMIT] + (" 💭" if thinking else " ▍")) if shown else "💭 думаю…"
                    if view != last_view:
                        last_view = view
                        await _safe_edit(placeholder, view)
    except OllamaError as exc:
        log.warning("Ollama stream failed: %s", exc)
        if not strip_think(buffer):
            await placeholder.edit_text(render_ollama_error(exc), reply_markup=kb.chat_kb())
            return None
        interrupted = True

    final = strip_think(buffer) or "(пустой ответ)"
    if interrupted:
        final += "\n\n⚠️ ответ оборван"
    chunks = split_message(final) or [final]
    await _safe_edit(placeholder, chunks[0], kb.chat_kb() if len(chunks) == 1 else None, final=True)
    for index, chunk in enumerate(chunks[1:], start=2):
        await message.answer(chunk, parse_mode=None, reply_markup=kb.chat_kb() if index == len(chunks) else None)
    return final


async def _source_text(message: Message, bot: Bot, http: aiohttp.ClientSession) -> tuple[str | None, str | None]:
    """(заголовок, текст для резюме) из документа, ссылки или самого сообщения."""
    document = message.document
    if document is not None:
        if document.file_size and document.file_size > MAX_DOWNLOAD:
            raise WebTextError("файл больше 15 МБ")
        buffer = io.BytesIO()
        await bot.download(document, destination=buffer)
        text = await asyncio.to_thread(document_to_text, buffer.getvalue(), document.file_name or "", document.mime_type)
        return document.file_name, text

    text = _incoming_text(message)
    if text is None:
        return None, None
    urls = extract_urls(text)
    # Ссылка без существенного пояснения — резюмируем страницу; иначе — сам текст.
    if urls and len(text) - len(urls[0]) < 40:
        return await fetch_text(http, urls[0])
    return None, text


# ------------------------------------------------------------------ помощники
async def _panel(ollama: OllamaService) -> tuple[str, InlineKeyboardMarkup]:
    try:
        loaded = await ollama.loaded_models()
        error = None
    except OllamaError as exc:
        loaded, error = [], str(exc)
    text = render_ollama_panel(host=ollama.host, active_model=ollama.active_model, loaded=loaded, error=error)
    return text, kb.ollama_kb()


def _incoming_text(message: Message) -> str | None:
    text = (message.text or message.caption or "").strip()
    return text or None


async def _reply_plain(message: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Ответ модели уходит БЕЗ разметки: в нём могут быть `<`, `*`, `_`, ломающие парсер Telegram."""
    chunks = split_message(text) or ["(пустой ответ)"]
    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1
        await message.answer(chunk, parse_mode=None, reply_markup=markup if is_last else None)


async def _show_models(call: CallbackQuery, state: FSMContext, ollama: OllamaService) -> None:
    try:
        models = await ollama.list_models()
    except OllamaError as exc:
        await edit_or_send(call, render_ollama_error(exc), kb.ollama_kb())
        return
    if not models:
        await edit_or_send(
            call,
            "В Ollama пока нет скачанных моделей.\nСкачайте, например: <code>ollama pull llama3.2</code>",
            kb.ollama_kb(),
        )
        return
    await state.update_data(models=models)  # callback_data несёт только индекс — список храним в FSM
    await edit_or_send(
        call,
        f"🔁 <b>Выбор модели</b>\nСейчас: <code>{html.escape(ollama.active_model)}</code>",
        kb.models_kb(models, ollama.active_model),
    )


# ---------------------------------------------------------------------- панель
@router.message(Command("ollama"))
async def cmd_ollama(message: Message, state: FSMContext, ollama: OllamaService) -> None:
    await state.clear()
    text, markup = await _panel(ollama)
    await message.answer(text, reply_markup=markup)


@router.callback_query(MenuCb.filter(F.action == MenuAction.OLLAMA))
@router.callback_query(OllamaCb.filter(F.action == OllamaAction.PANEL))
async def cb_panel(call: CallbackQuery, state: FSMContext, ollama: OllamaService) -> None:
    await call.answer()
    await state.clear()
    text, markup = await _panel(ollama)
    await edit_or_send(call, text, markup)


# ------------------------------------------------------------------------ чат
@router.callback_query(OllamaCb.filter(F.action == OllamaAction.CHAT))
async def cb_chat_start(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await state.set_state(OllamaStates.chat)
    await state.update_data(history=[])
    if isinstance(call.message, Message):
        await send_picture(call.message, UNI_THINK, CHAT_INTRO, kb.chat_kb())
    else:
        await edit_or_send(call, CHAT_INTRO, kb.chat_kb())


@router.message(OllamaStates.chat, ~F.text.startswith("/"))  # /status, /news и т.п. работают и в чате
async def on_chat_message(message: Message, state: FSMContext, ollama: OllamaService, bot: Bot) -> None:
    text = _incoming_text(message)
    if text is None:
        await message.answer(_EMPTY_HINT, reply_markup=kb.chat_kb())
        return
    if text.startswith("/"):
        await message.answer("Неизвестная команда. Выйти из чата: /cancel", reply_markup=kb.chat_kb())
        return

    data = await state.get_data()
    history = [*data.get("history", []), {"role": "user", "content": text}]
    reply = await _stream_reply(message, ollama, history)
    if reply is None:
        return  # историю не трогаем: неудавшийся вопрос в неё не попадает
    history.append({"role": "assistant", "content": reply})
    await state.update_data(history=history[-ollama.max_history :])


# ------------------------------------------------------------- резюме текста
@router.callback_query(OllamaCb.filter(F.action == OllamaAction.SUMMARIZE))
async def cb_summarize_start(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await state.set_state(OllamaStates.summarize)
    await edit_or_send(call, SUMMARIZE_INTRO, kb.chat_kb())


@router.message(OllamaStates.summarize, ~F.text.startswith("/"))
async def on_summarize_message(message: Message, ollama: OllamaService, bot: Bot, http: aiohttp.ClientSession) -> None:
    if (message.text or "").startswith("/"):
        await message.answer("Неизвестная команда. Выйти из режима: /cancel", reply_markup=kb.chat_kb())
        return
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
            title, text = await _source_text(message, bot, http)
            if text is None:
                await message.answer("Пришлите текст, ссылку или PDF/текстовый файл.", reply_markup=kb.chat_kb())
                return
            summary = await ollama.summarize(text)
    except WebTextError as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}", reply_markup=kb.chat_kb())
        return
    except OllamaError as exc:
        log.warning("Ollama summarize failed: %s", exc)
        await message.answer(render_ollama_error(exc), reply_markup=kb.chat_kb())
        return
    header = f"📝 {title}\n\n" if title else ""
    await _reply_plain(message, header + summary, kb.chat_kb())


# -------------------------------------------------------------------- модели
@router.callback_query(OllamaCb.filter(F.action == OllamaAction.MODELS))
async def cb_models(call: CallbackQuery, state: FSMContext, ollama: OllamaService) -> None:
    await call.answer()
    await _show_models(call, state, ollama)


@router.callback_query(OllamaCb.filter(F.action == OllamaAction.PICK))
async def cb_pick_model(call: CallbackQuery, callback_data: OllamaCb, state: FSMContext, ollama: OllamaService) -> None:
    models: list[str] = (await state.get_data()).get("models") or []
    if not 0 <= callback_data.idx < len(models):
        await call.answer("Список устарел — обновляю")
        await _show_models(call, state, ollama)
        return
    chosen = models[callback_data.idx]
    await ollama.set_active_model(chosen)
    await call.answer(f"Модель: {chosen}"[:200])
    text, markup = await _panel(ollama)
    await edit_or_send(call, text, markup)


# --------------------------------------------------------- выгрузка из памяти
@router.callback_query(OllamaCb.filter(F.action == OllamaAction.UNLOAD))
async def cb_unload(call: CallbackQuery, state: FSMContext, ollama: OllamaService) -> None:
    try:
        unloaded = await ollama.unload()
    except OllamaError as exc:
        log.warning("Ollama unload failed: %s", exc)
        await call.answer(f"Не удалось выгрузить: {exc}"[:200], show_alert=True)
        return

    if unloaded:
        await call.answer(f"🧹 Выгружено из RAM: {', '.join(unloaded)}"[:200], show_alert=True)
    else:
        await call.answer("Загруженных моделей нет — RAM уже свободна", show_alert=True)

    if await state.get_state() is None:  # мы на панели — обновим цифры; в режиме чата сообщение не трогаем
        text, markup = await _panel(ollama)
        await edit_or_send(call, text, markup)
