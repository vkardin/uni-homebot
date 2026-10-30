"""Тексты раздела «🔎 Сайты». Без aiogram — проверяются обычными тестами."""

from __future__ import annotations

import html
from datetime import datetime, tzinfo

from homebot.services.sites import Change, PriceStats, Site, change_percent, domain
from homebot.ui.style import DIV

esc = html.escape

OFF_TEXT = (
    "🔎 <b>Сайты</b>\n" + DIV + "\n"
    "Не подключено. Нужен контейнер changedetection.io и его ключ API: в <code>.env</code> "
    "<code>CHANGEDETECTION_URL</code> и <code>CHANGEDETECTION_API_KEY</code> "
    "(ключ — в changedetection: Settings → API; <code>deploy/update.sh</code> подставляет его сам)."
)

ADD_PROMPT = (
    "➕ <b>Новая слежка</b>\n"
    "Пришлите ссылку на товар в любом магазине или на любую страницу — "
    "скажу, когда изменится цена или текст. Можно сразу несколько ссылок (до 10), каждую с новой строки."
)
TARGET_PROMPT = (
    "🎯 <b>Цена-цель</b>\n"
    "Напишите цену, при которой сообщить, например <code>1100</code>.\n"
    "Скажу один раз, когда цена опустится до неё, и снова — только после того, как она поднимется выше."
)
BAD_PRICE = "Не разобрал число. Напишите цену цифрами, например <code>1100</code>, или нажмите «Отмена»."
BROWSER_HINT = (
    "Откройте эту страницу браузером, если сайт не пускает обычные запросы. Нужен браузерный контейнер в changedetection (см. README)."
)
BAD_URL = "Не вижу ссылки. Пришлите адрес страницы, например <code>https://example.com/item/…</code>, или нажмите «Отмена»."
BLOCKED_HINT = (
    "Сайт не пускает обычные запросы (защита от ботов). Включите «🌐 Браузер» в карточке слежки — "
    "для этого в changedetection нужен браузерный контейнер (инструкция в README)."
)
BLOCKED_BROWSER_HINT = (
    "Сайт не пускает даже браузер. Возможно, браузерный контейнер в changedetection не настроен (см. README) "
    "или сайт особенно строгий."
)


def money(value: float, currency: str) -> str:
    text = f"{value:,.0f}" if abs(value - round(value)) < 0.005 else f"{value:,.2f}"
    return f"{text.replace(',', ' ')} {currency}"


def ago(ts: int, now: float) -> str:
    if ts <= 0:
        return "ещё нет"
    delta = max(0, int(now - ts))
    if delta < 90:
        return "только что"
    if delta < 3600:
        return f"{delta // 60} мин назад"
    if delta < 86400:
        return f"{delta // 3600} ч назад"
    return f"{delta // 86400} дн назад"


def every(minutes: int) -> str:
    if minutes <= 0:
        return "как в changedetection"
    if minutes < 60:
        return f"{minutes} мин"
    if minutes % 1440 == 0:
        days = minutes // 1440
        return "сутки" if days == 1 else f"{days} дн"
    return f"{minutes // 60} ч"


def _stock(value: bool | None) -> str:
    return "в наличии" if value else "нет в наличии" if value is False else "неизвестно"


def _price_text(site: Site, currency: str) -> str:
    if site.price is None:
        return "цену не нашёл"
    text = money(site.price, currency)
    if site.last_price is not None and site.last_price != site.price:
        text += f" (было {money(site.last_price, currency)}, {change_percent(site.last_price, site.price):+.1f}%)"
    return text


def _list_line(index: int, site: Site, currency: str, now: float) -> str:
    icon = "⏸" if site.paused else "⚠️" if site.error else "💰" if site.is_price else "📄"
    tail = ""
    if site.is_price and site.price is not None:
        arrow = ""
        if site.last_price is not None and site.last_price != site.price:
            arrow = " 📉" if site.price < site.last_price else " 📈"
        tail = f" — <b>{money(site.price, currency)}</b>{arrow}"
        if site.in_stock is False:
            tail += " · нет в наличии"
    elif site.last_changed:
        tail = f" — изменилось {ago(site.last_changed, now)}"
    return f"{index}. {icon} {esc(site.name[:60])}{tail}"


def render_sites(sites: list[Site], total: int, currency: str, now: float) -> str:
    lines = ["🔎 <b>Сайты</b>", DIV]
    if not sites:
        lines += [
            "Пока ничего не отслеживается.",
            "Нажмите «➕ Добавить», пришлите ссылку на товар — и я сообщу, когда изменится цена.",
        ]
        return "\n".join(lines)
    paused = sum(1 for s in sites if s.paused)
    lines.append(f"Слежек: <b>{total}</b>" + (f" · на паузе: {paused}" if paused else ""))
    lines.append("")
    lines += [_list_line(i, s, currency, now) for i, s in enumerate(sites, 1)]
    if total > len(sites):
        lines += ["", f"<i>Показаны первые {len(sites)}, остальные — в самом changedetection.</i>"]
    return "\n".join(lines)


def _day(ts: int, tz: tzinfo | None) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m")


def render_site_card(
    site: Site,
    currency: str,
    now: float,
    stats: PriceStats | None = None,
    target: float | None = None,
    tz: tzinfo | None = None,
) -> str:
    icon = "💰" if site.is_price else "📄"
    lines = [f"{icon} <b>{esc(site.name)}</b>", DIV, f"🔗 {esc(domain(site.url))}" + (" · 🌐 браузер" if site.browser else "")]
    if site.is_price:
        lines.append(f"Цена: <b>{_price_text(site, currency)}</b>")
        if site.price is not None or site.in_stock is not None:
            lines.append(f"Наличие: {_stock(site.in_stock)}")
        if stats is not None and stats.points >= 2:
            lines.append(
                f"За всё время: от {money(stats.lowest, currency)} ({_day(stats.lowest_at, tz)}) "
                f"до {money(stats.highest, currency)} ({_day(stats.highest_at, tz)})"
            )
        if target:
            lines.append(f"🎯 Цель: до {money(target, currency)}")
    else:
        lines.append("Режим: любые изменения страницы")
    state = "⏸ на паузе" if site.paused else f"каждые {every(site.interval_min)}" if site.interval_min else every(0)
    lines.append(f"Проверка: {state} · последняя: {ago(site.last_checked, now)}")
    lines.append(f"Изменялось: {ago(site.last_changed, now)}")
    if site.error:
        lines += ["", f"⚠️ {esc(site.error)}"]
        if site.blocked:
            lines.append(f"<i>{BLOCKED_BROWSER_HINT if site.browser else BLOCKED_HINT}</i>")
    elif site.is_price and site.price is None and site.last_checked:
        lines += [
            "",
            "<i>Цену на этой странице найти не удалось. Если это не страница одного товара, удалите слежку "
            "и добавьте её как «Любые изменения».</i>",
        ]
    return "\n".join(lines)


def render_mode_prompt(urls: list[str], skipped: int = 0) -> str:
    head = (
        f"🔗 <b>{esc(domain(urls[0]))}</b>"
        if len(urls) == 1
        else f"🔗 Ссылок: <b>{len(urls)}</b> ({esc(', '.join(domain(u) for u in urls[:4]))}…)"
    )
    extra = f"\nУже отслеживаются и пропущены: {skipped}." if skipped else ""
    return (
        f"{head}{extra}\n{DIV}\n"
        "Что отслеживать?\n"
        "• <b>Цена товара</b> — для страницы одного товара: сообщу, когда цена или наличие изменятся.\n"
        "• <b>Любые изменения</b> — для списков, объявлений, новостей: покажу, что добавилось и что пропало."
    )


def render_adding(urls: list[str]) -> str:
    what = esc(domain(urls[0])) if len(urls) == 1 else f"{len(urls)} ссылок"
    return f"⏳ Добавляю <b>{what}</b> и проверяю страницы…"


def render_delete_confirm(site: Site) -> str:
    return f"🗑 Удалить слежку за <b>{esc(site.name)}</b>?\nИстория изменений в changedetection тоже пропадёт."


def render_site_change(site: Site, change: Change, currency: str, *, target_hit: float | None = None, record_low: bool = False) -> str:
    name = esc(site.name)
    link = esc(domain(site.url))
    if change.kind == "price":
        old, new = change.old_price, change.new_price
        lines: list[str] = []
        if old is not None and new is not None and old != new:
            icon = "📉" if new < old else "📈"
            title = "Цена упала" if new < old else "Цена выросла"
            lines += [
                f"{icon} <b>{title}</b> · {name}",
                f"{money(old, currency)} → <b>{money(new, currency)}</b> ({change_percent(old, new):+.1f}%)",
            ]
        elif new is not None and old is None:
            lines += [f"💰 <b>Появилась цена</b> · {name}", f"<b>{money(new, currency)}</b>"]
        elif new is None and old is not None:
            lines += [f"❓ <b>Цена пропала со страницы</b> · {name}", f"Было {money(old, currency)}"]
        else:
            lines.append(f"🔔 <b>Изменение</b> · {name}")
        if change.old_stock != change.new_stock and change.new_stock is not None:
            lines.append("✅ Появился в наличии" if change.new_stock else "❌ Закончился")
        elif new is not None:
            lines.append(_stock(change.new_stock).capitalize())
        if record_low:
            lines.append("🔥 Самая низкая цена за всё время наблюдений")
        if target_hit:
            lines.append(f"🎯 Это ниже вашей цели ({money(target_hit, currency)})")
        lines.append(f"🔗 {link}")
        return "\n".join(lines)
    lines = [f"🔎 <b>Страница изменилась</b> · {name}"]
    if change.added:
        lines += ["", "➕ <b>Добавилось</b>"] + [f"• {esc(item)}" for item in change.added]
    if change.removed:
        lines += ["", "➖ <b>Пропало</b>"] + [f"• {esc(item)}" for item in change.removed]
    if not change.added and not change.removed:
        lines += ["", "<i>Изменения мелкие — подробности в changedetection.</i>"]
    lines += ["", f"🔗 {link}"]
    return "\n".join(lines)


def render_site_error(site: Site) -> str:
    lines = [f"⚠️ <b>Не могу открыть страницу</b> · {esc(site.name)}", esc(site.error)]
    if site.blocked:
        lines += ["", f"<i>{BLOCKED_HINT}</i>"]
    return "\n".join(lines)
