"""Тексты раздела «💱 P2P-курс». Без aiogram — проверяются обычными тестами."""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import datetime, tzinfo

from homebot.services.p2p import P2PAd, P2PQuote, P2PStats, change_percent
from homebot.services.prefs import P2PPrefs
from homebot.ui.style import DIV

esc = html.escape

_SYMBOLS = {"UAH": "₴", "USD": "$", "EUR": "€", "PLN": "zł", "RUB": "₽"}
OFF_TEXT = "💱 <b>P2P-курс</b>\n" + DIV + "\nВыключен: в .env задано <code>P2P_ENABLED=false</code>."


def money(value: float, fiat: str) -> str:
    return f"{value:,.2f}".replace(",", " ") + f" {_SYMBOLS.get(fiat.upper(), fiat.upper())}"


def arrow(percent: float) -> str:
    return "📈" if percent > 0 else "📉" if percent < 0 else "➖"


def _sign(percent: float) -> str:
    return f"{percent:+.1f}%"


def _ad_line(ad: P2PAd, fiat: str) -> str:
    limits = ""
    if ad.min_amount is not None and ad.max_amount is not None:
        limits = f" · лимит {ad.min_amount:,.0f}–{ad.max_amount:,.0f}".replace(",", " ")
    methods = f" · {esc(', '.join(ad.methods[:2]))}" if ad.methods else ""
    who = f" <i>{esc(ad.merchant[:18])}</i>" if ad.merchant else ""
    return f"• <b>{money(ad.price, fiat)}</b>{limits}{methods}{who}"


def prefs_lines(prefs: P2PPrefs, fiat: str) -> list[str]:
    change = f"±{prefs.threshold:g}%" if prefs.threshold else "выключено"
    target = money(prefs.target, fiat) if prefs.target else "не задана"
    return [
        f"🔔 Резкое изменение: <b>{change}</b>",
        f"🎯 Цель: <b>{target}</b>",
        f"🌆 Вечерняя сводка: <b>{'вкл' if prefs.summary else 'выкл'}</b>",
    ]


def render_p2p(
    quote: P2PQuote | None,
    *,
    error: str,
    day: P2PStats | None,
    prefs: P2PPrefs,
    asset: str,
    fiat: str,
    amount: float,
    methods: Sequence[str],
    tz: tzinfo | None,
) -> str:
    lines = [f"💱 <b>Продать {esc(asset)} за {esc(fiat)}</b> · Binance P2P", DIV]
    if quote is None:
        lines.append(f"⚠️ Не получилось получить курс: {esc(error)}" if error else "Курс ещё не получен.")
    else:
        when = datetime.fromtimestamp(quote.at, tz).strftime("%H:%M")
        lines.append(f"💵 Типичная цена: <b>{money(quote.typical, fiat)}</b>  <i>(медиана лучших, {when})</i>")
        lines.append(f"🥇 Лучшая: <b>{money(quote.best, fiat)}</b>")
        if day is not None and day.points > 1:
            lines.append(
                f"📊 За сутки: {money(day.low, fiat)} … {money(day.high, fiat)} · "
                f"{arrow(day.change_percent)} {_sign(day.change_percent)}"
            )
        lines += ["", "<b>Лучшие объявления</b>", *(_ad_line(ad, fiat) for ad in quote.ads)]
    filters = []
    if amount > 0:
        filters.append(f"сумма {amount:,.0f}".replace(",", " "))
    if methods:
        filters.append(", ".join(methods))
    lines += ["", f"<i>Фильтр: {esc('; '.join(filters)) if filters else 'любые объявления'}</i>", DIV, *prefs_lines(prefs, fiat)]
    return "\n".join(lines)


def render_p2p_change(quote: P2PQuote, previous: float, *, asset: str, fiat: str) -> str:
    percent = change_percent(previous, quote.typical)
    return (
        f"{arrow(percent)} <b>{esc(asset)} → {esc(fiat)}</b>: {money(quote.typical, fiat)} "
        f"({_sign(percent)} к {money(previous, fiat)})\n"
        f"🥇 лучшая сейчас {money(quote.best, fiat)}"
    )


def render_p2p_target(quote: P2PQuote, target: float, *, asset: str, fiat: str) -> str:
    return (
        f"🎯 <b>Цель достигнута</b>: {esc(asset)} продаётся по {money(quote.typical, fiat)} "
        f"(цель {money(target, fiat)})\n🥇 лучшая сейчас {money(quote.best, fiat)}"
    )


def render_p2p_summary(quote: P2PQuote, day: P2PStats | None, *, asset: str, fiat: str) -> str:
    lines = [
        f"🌆 <b>{esc(asset)} → {esc(fiat)}: итог дня</b>",
        f"Сейчас: <b>{money(quote.typical, fiat)}</b> · лучшая {money(quote.best, fiat)}",
    ]
    if day is not None and day.points > 1:
        lines += [
            f"Минимум: {money(day.low, fiat)} · максимум: {money(day.high, fiat)} · в среднем {money(day.average, fiat)}",
            f"За сутки {arrow(day.change_percent)} {_sign(day.change_percent)}",
        ]
    return "\n".join(lines)
