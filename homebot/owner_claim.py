"""Мастер первого запуска: ждём владельца по коду из журнала (см. services/owner.py).

Работает до основного диспетчера и без него: читает только личные текстовые сообщения через getUpdates,
никаких кнопок и команд. Пока владельца нет, бот ничего больше не умеет — так чужой человек, нашедший
бота раньше хозяина, ничего не получит.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramServerError

from homebot.services import owner

log = logging.getLogger(__name__)

TEXTS = {
    "no_code": "👋 Это новый UNI. Чтобы стать владельцем, отправьте <code>/start КОД</code> — код в журнале сервера: "
    "<code>journalctl -u homebot | grep start</code> (или в файле owner-code.txt в каталоге данных).",
    "wrong": "❌ Код не подошёл.",
    "blocked": "⛔ Слишком много попыток.",
    "ok": "✅ Готово, вы владелец. Бот запускается — через несколько секунд нажмите /start.",
}


async def wait_owner(bot: Bot, state_dir: Path) -> int:
    claim = owner.Claim(state_dir)
    claim.publish()
    with suppress(TelegramAPIError):
        await bot.delete_webhook(drop_pending_updates=True)
    offset = 0
    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=25, allowed_updates=["message"])
        except (TelegramNetworkError, TelegramServerError) as exc:
            log.warning("getUpdates failed (%s); retry in 5 s", exc)
            await asyncio.sleep(5)
            continue
        for update in updates:
            offset = update.update_id + 1
            msg = update.message
            if msg is None or msg.chat.type != "private" or msg.from_user is None or not msg.text:
                continue
            result = claim.check(msg.from_user.id, msg.text)
            if result == "blocked" and claim.tries.get(msg.from_user.id, 0) > owner.MAX_TRIES_PER_USER:
                continue  # молчим, чтобы не отвечать на каждый перебор
            if result == "blocked":
                claim.tries[msg.from_user.id] += 1
            with suppress(TelegramAPIError):
                await bot.send_message(msg.chat.id, TEXTS[result])
            if result == "ok":
                owner.save(state_dir, msg.from_user.id)
                log.warning("Владелец назначен: %s", msg.from_user.id)
                with suppress(TelegramAPIError):  # подтверждаем полученные обновления
                    await bot.get_updates(offset=offset, timeout=0)
                return msg.from_user.id
