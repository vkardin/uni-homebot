"""Фирменный стиль UNI: имя, голос, разделители, приветствия.

UNI — дружелюбный робот-помощник домашнего сервера. Говорит от первого лица, коротко и по делу,
без канцелярита. Визуально — «неоновый пульт»: эмодзи-иконки разделов, тонкий разделитель под
заголовком, в графиках — циан и фиолет на тёмно-синем фоне (как на аватарке).
"""

from __future__ import annotations

from datetime import datetime, tzinfo

NAME = "UNI"
DIV = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"

# Палитра аватарки — используется в графиках.
NEON_CYAN = "#3ddcff"
NEON_PURPLE = "#b06cff"
NEON_PINK = "#ff6bd5"
NEON_AMBER = "#ffb84d"
DEEP_BG = "#0b1030"
PANEL_BG = "#121a45"
GRID = "#24306b"
TEXT = "#d6e2ff"

BOT_SHORT_DESCRIPTION = "UNI — робот-помощник домашнего сервера: новости каждый час, напоминания, присмотр за сервером."
BOT_DESCRIPTION = (
    "Привет! Я UNI 🤖\n\n"
    "🗞 Каждый час присылаю свежие новости в коротком пересказе\n"
    "⏰ Напоминаю о делах и храню заметки\n"
    "💰 Веду траты и бюджет в Actual Budget\n"
    "🖥 Присматриваю за сервером, Docker и сервисами\n"
    "🤖 Отвечаю на вопросы с помощью локальной нейросети\n\n"
    "Я работаю только для своего владельца."
)

ASSISTANT_PROMPT = (
    "Ты — UNI, дружелюбный робот-помощник домашнего сервера. Отвечай по-русски, тепло, но коротко и по делу, "
    "без воды. Если не знаешь — честно скажи. Не выдумывай факты."
)


def greeting(now: datetime, tz: tzinfo | None = None) -> str:
    hour = now.astimezone(tz).hour if tz else now.hour
    if 5 <= hour < 12:
        return "Доброе утро"
    if 12 <= hour < 18:
        return "Добрый день"
    if 18 <= hour < 23:
        return "Добрый вечер"
    return "Доброй ночи"


def head(icon: str, title: str) -> str:
    return f"{icon} <b>{title}</b>\n{DIV}"


def main_menu_text(now: datetime | None = None, tz: tzinfo | None = None) -> str:
    hello = greeting(now, tz) if now else "Привет"
    return (
        f"🤖 <b>{NAME} на связи</b> · {hello}!\n{DIV}\n"
        "🗞 Новости каждый час · 📰 сводка дня\n"
        "⏰ Напоминания · 📝 заметки · 🤖 ассистент\n"
        "🖥 Присмотр за сервером\n\n"
        "Чем займёмся?"
    )
