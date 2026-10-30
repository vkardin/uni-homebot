"""Стартовый экран: приветствие, одна строка «погода · курс · сервер», пара коротких новостей."""

from __future__ import annotations

import html
from datetime import datetime, tzinfo

from homebot.services.expense_parse import format_money
from homebot.services.home import HomeData, short
from homebot.ui.style import DIV, NAME, greeting

esc = html.escape

_CURRENCY_ICONS = {"USD": "💵", "EUR": "💶", "GBP": "💷", "PLN": "🇵🇱", "CHF": "🇨🇭"}
_MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")
FALLBACK_BODY = "🗞 Новости каждый час · 📰 сводка дня\n⏰ Напоминания · 📝 заметки · 🤖 ассистент\n🖥 Присмотр за сервером"


def _num(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def status_line(data: HomeData) -> str:
    parts: list[str] = []
    if data.weather is not None:
        w = data.weather
        parts.append(f"{w.emoji} {round(w.temperature):+d}°")
    for rate in data.rates:
        icon = _CURRENCY_ICONS.get(rate.code)
        parts.append(f"{icon} {_num(rate.rate)}" if icon else f"{rate.code} {_num(rate.rate)}")
    if data.cpu is not None:
        load = f"🖥 CPU {data.cpu:.0f}%"
        if data.ram is not None:
            load += f" · RAM {data.ram:.0f}%"
        parts.append(load)
    return " · ".join(parts)


def render_home(now: datetime, tz: tzinfo | None, data: HomeData | None, currency: str = "₴") -> str:
    lines = [f"🤖 <b>{NAME} на связи</b> · {greeting(now, tz)}!", DIV]
    if data is None:
        return "\n".join([*lines, FALLBACK_BODY, "", "Чем займёмся?"])
    if line := status_line(data):
        lines += [esc(line), DIV]
    for title in data.news:
        lines.append(f"🗞 {esc(short(title))}")
    if data.month_spent is not None:
        month = _MONTHS[now.astimezone(tz).month - 1] if tz else _MONTHS[now.month - 1]
        lines.append(f"💰 {month.capitalize()}: расход {esc(format_money(data.month_spent, currency))}")
    if lines[-1] == DIV:
        lines.pop()
    if len(lines) == 1:  # ни погоды, ни новостей — хотя бы расскажем, что умею
        lines += [DIV, FALLBACK_BODY]
    lines += ["", "Чем займёмся?"]
    return "\n".join(lines)
