"""🧭 Чат-версии экранов, которые раньше были только в панели (паритет 10.0): /rules, /servers, /home, /catalog."""

from __future__ import annotations

import asyncio
import html
from dataclasses import replace
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from homebot.config import Settings
from homebot.services import community, fleet, hass, rules
from homebot.services.apps_http import AppError
from homebot.utils.telegram import edit_or_send

router = Router(name="platform")
_PANEL_HINT = "\n\nДобавить и настроить: /app → Ещё → {}."


class RuleCb(CallbackData, prefix="rl"):
    id: str


class HomeCb(CallbackData, prefix="hs"):
    i: int  # номер в списке; сверяется по началу id
    k: str


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup | None:
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


# ---------------------------------------------------------------- ⚡ сценарии
def _rules_view(store: Any) -> tuple[str, InlineKeyboardMarkup | None]:
    items = rules.load(store)
    if not items:
        return "⚡ <b>Сценарии</b>\n\nПока нет ни одного." + _PANEL_HINT.format("Сценарии"), None
    lines = ["⚡ <b>Сценарии</b>", ""] + [("🟢 " if r.on else "⏸ ") + html.escape(rules.describe(r)) for r in items]
    buttons = [
        [InlineKeyboardButton(text=("⏸ Пауза " if r.on else "▶️ Включить ") + str(n), callback_data=RuleCb(id=r.id).pack())]
        for n, r in enumerate(items, 1)
    ]
    return "\n".join(lines) + _PANEL_HINT.format("Сценарии"), _kb(buttons)


@router.message(Command("rules"))
async def cmd_rules(message: Message, store: Any) -> None:
    text, markup = _rules_view(store)
    await message.answer(text, reply_markup=markup)


@router.callback_query(RuleCb.filter())
async def cb_rule(call: CallbackQuery, callback_data: RuleCb, store: Any) -> None:
    items = [replace(r, on=not r.on) if r.id == callback_data.id else r for r in rules.load(store)]
    await rules.save(store, items)
    await call.answer("Готово")
    text, markup = _rules_view(store)
    await edit_or_send(call, text, markup)


# ------------------------------------------------------------ 🛰 другие машины
@router.message(Command("servers", "fleet"))
async def cmd_servers(message: Message, settings: Settings, http: Any, store: Any) -> None:
    agents = fleet.load(settings.state_dir)
    if not agents:
        await message.answer("🛰 <b>Другие машины</b>\n\nПока не подключено ни одной." + _PANEL_HINT.format("Другие машины"))
        return

    async def one(a: fleet.Agent) -> str:
        try:
            m = await fleet.metrics(http, a)
        except Exception as exc:
            return f"🔴 <b>{html.escape(a.name)}</b>: не отвечает ({html.escape(str(exc)[:80])})"
        parts = [f"CPU {m.get('cpu', 0):.0f}%", f"RAM {m.get('ram', 0):.0f}%"]
        if m.get("disk") is not None:
            parts.append(f"диск {m['disk']:.0f}%")
        if m.get("temp") is not None:
            parts.append(f"{m['temp']:.0f}°C")
        return f"🟢 <b>{html.escape(a.name)}</b>: " + " · ".join(parts) + (" · 💾 копии" if a.backups else "")

    lines = await asyncio.gather(*(one(a) for a in agents))
    await message.answer("🛰 <b>Другие машины</b>\n\n" + "\n".join(lines))


# ------------------------------------------------------------ 🏠 Home Assistant
async def _home_view(service: Any) -> tuple[str, InlineKeyboardMarkup | None]:
    items = await service.entities()
    lines, rows = ["🏠 <b>Дом</b>", ""], []
    for i, e in enumerate(items):
        if e.domain in hass.TOGGLE or e.domain in hass.RUN:
            mark = "▶️" if e.domain in hass.RUN else ("🟢" if e.on else "⚪")
            rows.append([InlineKeyboardButton(text=f"{mark} {e.name}"[:60], callback_data=HomeCb(i=i, k=e.id[:24]).pack())])
        else:
            lines.append(f"• {html.escape(e.name)}: {html.escape(e.state)} {html.escape(e.unit)}".rstrip())
    if len(lines) == 2 and not rows:
        lines.append("Устройств не нашлось.")
    return "\n".join(lines[:42]), _kb(rows[:30])


@router.message(Command("home", "house"))
async def cmd_home(message: Message, apps: Any) -> None:
    if apps.hass is None:
        await message.answer("🏠 Home Assistant не подключён. В чате: /connect, или HASS_URL и HASS_TOKEN в .env.")
        return
    try:
        text, markup = await _home_view(apps.hass)
    except AppError as exc:
        await message.answer(f"🏠 Home Assistant не ответил: {html.escape(str(exc))}")
        return
    await message.answer(text, reply_markup=markup)


@router.callback_query(HomeCb.filter())
async def cb_home(call: CallbackQuery, callback_data: HomeCb, apps: Any) -> None:
    if apps.hass is None:
        await call.answer("Home Assistant не подключён", show_alert=True)
        return
    try:
        items = await apps.hass.entities()
        entity = items[callback_data.i] if callback_data.i < len(items) else None
        if entity is None or not entity.id.startswith(callback_data.k):
            await call.answer("Список изменился, открываю заново")
        else:
            await apps.hass.act(entity.id)
            await call.answer("Готово")
            await asyncio.sleep(0.6)
        text, markup = await _home_view(apps.hass)
    except AppError as exc:
        await call.answer(str(exc)[:180], show_alert=True)
        return
    await edit_or_send(call, text, markup)


# ------------------------------------------------------------ 🌍 каталог сообщества
@router.message(Command("catalog"))
async def cmd_catalog(message: Message, http: Any, settings: Settings, apps: Any) -> None:
    items = await community.load(http, settings.community_catalog_url) if http is not None else community.bundled()
    found: dict[str, str] = {}
    if apps.docker is not None:
        try:
            found = community.match(items, await apps.docker.containers_raw())
        except Exception:
            found = {}
    lines = ["🌍 <b>Каталог сообщества</b>", ""]
    for x in items[:40]:
        here = " — найден на сервере" if x["id"] in found else ""
        lines.append(f"{x.get('icon', '•')} {html.escape(str(x.get('title', '')))}{here}")
    lines.append(_PANEL_HINT.format("Каталог сообщества").strip())
    await message.answer("\n".join(lines))
