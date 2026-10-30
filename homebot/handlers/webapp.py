"""🖥 /app — открыть веб-панель (Telegram Mini App)."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from homebot.config import Settings
from homebot.ui import keyboards as kb

router = Router(name="webapp")

NOT_CONFIGURED = (
    "🖥 <b>Веб-панель</b>\nПанель уже запущена на сервере, но Telegram открывает такие страницы только по HTTPS-адресу.\n"
    "Выполните на сервере <code>sudo tailscale serve --bg 8088</code>, впишите выданный адрес в <code>.env</code> "
    "как <code>WEB_URL=https://…</code> и перезапустите бота (подробности — в README, раздел «Веб-панель»)."
)


@router.message(Command("app", "panel"))
async def cmd_app(message: Message, settings: Settings) -> None:
    if not settings.web_enabled:
        await message.answer("🖥 Веб-панель выключена (WEB_ENABLED=false).")
        return
    if not settings.web_url:
        await message.answer(NOT_CONFIGURED)
        return
    await message.answer("🖥 <b>Панель UNI</b> — весь функционал бота в красивом окне.", reply_markup=kb.webapp_kb(settings.web_url))
