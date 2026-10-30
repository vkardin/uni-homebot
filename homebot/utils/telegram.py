"""Небольшие помощники для работы с Telegram API."""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, LinkPreviewOptions, Message

log = logging.getLogger(__name__)


async def edit_or_send(
    call: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    preview: str | None = None,
) -> None:
    """Обновляет сообщение с кнопкой; если нельзя отредактировать — шлёт новое.

    * «message is not modified» (нажали «Обновить», а данные те же) — не ошибка.
    * Недоступное (устаревшее) сообщение — просим открыть меню заново.
    * `preview` — ссылка на картинку (постер), которая показывается крупно над текстом.
    """
    message = call.message
    if not isinstance(message, Message):
        await call.answer("Сообщение устарело — откройте /menu", show_alert=True)
        return

    options = link_preview(preview)
    try:
        await message.edit_text(text, reply_markup=reply_markup, link_preview_options=options)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        log.debug("edit_text failed (%s), sending a new message instead", exc)
        await message.answer(text, reply_markup=reply_markup, link_preview_options=options)


def link_preview(url: str | None) -> LinkPreviewOptions:
    """Постер над текстом — или явно без превью (чтобы старая картинка не «залипала» после правки)."""
    if url:
        return LinkPreviewOptions(url=url, prefer_large_media=True, show_above_text=True)
    return LinkPreviewOptions(is_disabled=True)


async def answer_from_callback(
    call: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    parse_mode: str | None = "HTML",
) -> None:
    """Отправляет НОВОЕ сообщение в чат, где была нажата кнопка."""
    message = call.message
    if not isinstance(message, Message):
        await call.answer("Сообщение устарело — откройте /menu", show_alert=True)
        return
    await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)


ASSETS = Path(__file__).resolve().parent.parent / "assets"
UNI_THINK = ASSETS / "uni_think.jpg"


async def send_picture(message: Message, image: Path, caption: str, reply_markup: InlineKeyboardMarkup | None = None) -> Message:
    """Фото с подписью; если картинки нет или Telegram отказал — обычный текст (картинка лишь украшение)."""
    if image.exists():  # noqa: ASYNC240 - мгновенная проверка локального файла
        try:
            return await message.answer_photo(FSInputFile(image), caption=caption, reply_markup=reply_markup)
        except TelegramAPIError as exc:
            log.warning("Cannot send picture %s: %s", image.name, exc)
    return await message.answer(caption, reply_markup=reply_markup)
