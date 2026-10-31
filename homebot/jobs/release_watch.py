"""🆕 Раз в сутки смотрит релизы на GitHub и один раз сообщает о новой версии с кнопкой «Скачать»."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from homebot import __version__
from homebot.services import releases
from homebot.ui.callbacks import UpdateAction, UpdateCb
from homebot.ui.update_texts import esc

log = logging.getLogger(__name__)
KEY_NOTIFIED = "releases.notified"


class ReleaseWatchJob:
    def __init__(self, *, bot: Bot, admin_id: int, store: Any, http: Any, repo: str, channel: str, interval: int = 86400) -> None:
        self._bot, self._admin_id, self._store, self._http = bot, admin_id, store, http
        self._repo, self._channel, self._interval = repo, channel, interval

    async def tick(self) -> releases.Release | None:
        rel = releases.newest(await releases.fetch(self._http, self._repo), __version__, self._channel)
        if rel is None or self._store.get(KEY_NOTIFIED) == rel.version:
            return None
        beta = " (тестовая)" if rel.prerelease else ""
        text = f"🆕 Вышла <b>UNI {esc(rel.version)}</b>{beta}. Сейчас стоит {esc(__version__)}."
        text += "\n🔏 Есть подпись автора." if rel.sig_url else ""
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬇️ Скачать и проверить", callback_data=UpdateCb(action=UpdateAction.FETCH, sig=rel.version).pack()
                    )
                ]
            ]
            + ([[InlineKeyboardButton(text="Что нового", url=rel.page)]] if rel.page.startswith("https://") else [])
        )
        await self._bot.send_message(self._admin_id, text, reply_markup=markup)
        await self._store.set(KEY_NOTIFIED, rel.version)
        return rel

    async def run(self) -> None:
        await asyncio.sleep(120)  # после запуска не мешаем остальному
        while True:
            try:
                await self.tick()
            except (TelegramAPIError, OSError, ValueError) as exc:
                log.info("Release check failed: %s", exc)
            except Exception as exc:  # aiohttp.ClientError и прочее сетевое
                log.info("Release check failed: %s", exc)
            await asyncio.sleep(self._interval)
