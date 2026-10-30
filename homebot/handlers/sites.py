"""🔎 Сайты: слежка за ценами и страницами (changedetection.io) — список, добавление по ссылке, пауза, частота."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from homebot.config import Settings
from homebot.services.apps import Apps
from homebot.services.apps_http import AppError
from homebot.services.charts import NoDataError, render_price_chart
from homebot.services.p2p import parse_price
from homebot.services.power import is_fresh
from homebot.services.prefs import next_choice
from homebot.services.sites import INTERVAL_CHOICES, SITES_SHOWN, Site, SitesService, extract_urls, price_stats, set_target, target_of
from homebot.services.state import StateStore
from homebot.states import SitesStates
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, MenuCb, SitesAction, SitesCb
from homebot.ui.sites_texts import (
    ADD_PROMPT,
    BAD_PRICE,
    BAD_URL,
    OFF_TEXT,
    TARGET_PROMPT,
    money,
    render_adding,
    render_delete_confirm,
    render_mode_prompt,
    render_site_card,
    render_sites,
)
from homebot.utils.telegram import edit_or_send

log = logging.getLogger(__name__)


def esc_line(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


router = Router(name="sites")

_FIRST_CHECK_WAIT_SEC = 20  # сколько ждём первую проверку новой слежки, прежде чем показать карточку
_RECHECK_WAIT_SEC = 6


async def _list_view(service: SitesService, settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    sites, total = await service.sites(SITES_SHOWN)
    return render_sites(sites, total, settings.sites_currency, time.time()), kb.sites_kb(sites)


async def _card_view(service: SitesService, uuid: str, settings: Settings, store: StateStore) -> tuple[str, InlineKeyboardMarkup]:
    site = await service.details(uuid)
    stats = None
    if site.is_price:
        try:
            stats = price_stats(await service.price_history(uuid))
        except AppError:
            stats = None
    target = target_of(store, uuid)
    text = render_site_card(site, settings.sites_currency, time.time(), stats, target, settings.tz)
    return text, kb.site_kb(site, has_target=bool(target))


async def _wait_checked(service: SitesService, uuid: str, *, since: int, limit: float) -> None:
    """Ждём, пока changedetection (пере)проверит страницу: `last_checked` станет новее `since`."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        try:
            site = await service.get(uuid)
        except AppError:
            return
        if site.last_checked > since or site.error:
            return


async def _show_list(call_or_message: CallbackQuery | Message, apps: Apps, settings: Settings) -> None:
    service = apps.sites
    assert service is not None
    try:
        text, markup = await _list_view(service, settings)
    except AppError as exc:
        text, markup = f"🔎 <b>Сайты</b>\nНе получилось связаться с changedetection: {exc}", kb.back_kb()
    if isinstance(call_or_message, CallbackQuery):
        await edit_or_send(call_or_message, text, markup)
    else:
        await call_or_message.answer(text, reply_markup=markup)


@router.message(Command("sites", "site"))
async def cmd_sites(message: Message, state: FSMContext, apps: Apps, settings: Settings) -> None:
    await state.clear()
    if apps.sites is None:
        await message.answer(OFF_TEXT)
        return
    await _show_list(message, apps, settings)


@router.callback_query(MenuCb.filter(F.action == MenuAction.SITES))
@router.callback_query(SitesCb.filter(F.action == SitesAction.HOME))
async def cb_home(call: CallbackQuery, state: FSMContext, apps: Apps, settings: Settings) -> None:
    await state.clear()
    await call.answer()
    if apps.sites is None:
        await edit_or_send(call, OFF_TEXT, kb.back_kb())
        return
    await _show_list(call, apps, settings)


@router.callback_query(SitesCb.filter(F.action == SitesAction.CARD))
async def cb_card(call: CallbackQuery, callback_data: SitesCb, apps: Apps, settings: Settings, store: StateStore) -> None:
    if apps.sites is None:
        await call.answer()
        return
    await call.answer()
    try:
        text, markup = await _card_view(apps.sites, callback_data.ref, settings, store)
    except AppError as exc:
        await edit_or_send(call, f"🔎 Слежка недоступна: {exc}\nВозможно, её уже удалили.", kb.sites_cancel_kb())
        return
    await edit_or_send(call, text, markup)


@router.callback_query(SitesCb.filter(F.action == SitesAction.ADD))
async def cb_add(call: CallbackQuery, state: FSMContext, apps: Apps) -> None:
    if apps.sites is None:
        await call.answer()
        return
    await call.answer()
    await state.set_state(SitesStates.url)
    await edit_or_send(call, ADD_PROMPT, kb.sites_cancel_kb())


@router.message(SitesStates.url, ~F.text.startswith("/"))
async def on_url(message: Message, state: FSMContext, apps: Apps, settings: Settings, store: StateStore) -> None:
    service = apps.sites
    if service is None:
        await state.clear()
        await message.answer(OFF_TEXT)
        return
    urls = extract_urls(message.text or "")
    if not urls:
        await message.answer(BAD_URL, reply_markup=kb.sites_cancel_kb())
        return
    try:
        known = {s.url.rstrip("/").lower(): s for s in await service.brief()}
    except AppError as exc:
        await message.answer(f"Не получилось связаться с changedetection: {exc}", reply_markup=kb.sites_cancel_kb())
        return
    fresh = [u for u in urls if u.rstrip("/").lower() not in known]
    if not fresh:
        await state.clear()
        existing = known[urls[0].rstrip("/").lower()]
        text, markup = await _card_view(service, existing.uuid, settings, store)
        await message.answer("Эту страницу я уже отслеживаю:\n\n" + text, reply_markup=markup)
        return
    await state.update_data(urls=fresh)
    await message.answer(render_mode_prompt(fresh, len(urls) - len(fresh)), reply_markup=kb.sites_mode_kb())


@router.callback_query(SitesCb.filter(F.action == SitesAction.MODE), SitesStates.url)
async def cb_mode(
    call: CallbackQuery, callback_data: SitesCb, state: FSMContext, apps: Apps, settings: Settings, store: StateStore
) -> None:
    service = apps.sites
    data = await state.get_data()
    urls = data.get("urls")
    if service is None or not isinstance(urls, list) or not urls:
        await call.answer("Ссылка потерялась — начните заново", show_alert=True)
        await state.clear()
        return
    await state.clear()
    await call.answer()
    await edit_or_send(call, render_adding(urls))
    added: list[str] = []
    failed: list[str] = []
    for url in urls:
        try:
            uuid = await service.add(url, price=callback_data.arg == "p", browser=settings.sites_browser)
            await service.recheck(uuid)
            added.append(uuid)
        except AppError as exc:
            failed.append(f"{url[:60]}: {exc}")
    if not added:
        await edit_or_send(call, "🔎 Не получилось добавить:\n" + "\n".join(failed), kb.sites_cancel_kb())
        return
    try:
        await _wait_checked(service, added[0], since=0, limit=_FIRST_CHECK_WAIT_SEC)
        if len(added) == 1:
            text, markup = await _card_view(service, added[0], settings, store)
            text = "✅ Слежка добавлена\n\n" + text
        else:
            text, markup = await _list_view(service, settings)
            text = f"✅ Добавлено слежек: {len(added)}\n\n" + text
    except AppError as exc:
        await edit_or_send(call, f"🔎 Добавил, но не получилось показать: {exc}", kb.sites_cancel_kb())
        return
    if failed:
        text += "\n\n⚠️ Не добавлены:\n" + "\n".join(esc_line(f) for f in failed)
    await edit_or_send(call, text, markup)


@router.callback_query(SitesCb.filter(F.action == SitesAction.CHECK))
async def cb_check(call: CallbackQuery, callback_data: SitesCb, apps: Apps, settings: Settings, store: StateStore) -> None:
    service = apps.sites
    if service is None:
        await call.answer()
        return
    try:
        before = await service.get(callback_data.ref)
        await service.recheck(callback_data.ref)
        await call.answer("🔄 Проверяю…")
        await _wait_checked(service, callback_data.ref, since=before.last_checked, limit=_RECHECK_WAIT_SEC)
        text, markup = await _card_view(service, callback_data.ref, settings, store)
    except AppError as exc:
        await call.answer(f"Не получилось: {exc}"[:190], show_alert=True)
        return
    await edit_or_send(call, text, markup)


@router.callback_query(SitesCb.filter(F.action == SitesAction.CHECK_ALL))
async def cb_check_all(call: CallbackQuery, apps: Apps, settings: Settings) -> None:
    service = apps.sites
    if service is None:
        await call.answer()
        return
    try:
        await service.recheck_all()
    except AppError as exc:
        await call.answer(f"Не получилось: {exc}"[:190], show_alert=True)
        return
    await call.answer("🔄 Запустил проверку всех — результаты придут уведомлениями, если что-то изменилось", show_alert=True)
    await _show_list(call, apps, settings)


@router.callback_query(SitesCb.filter(F.action == SitesAction.PAUSE))
async def cb_pause(call: CallbackQuery, callback_data: SitesCb, apps: Apps, settings: Settings, store: StateStore) -> None:
    service = apps.sites
    if service is None:
        await call.answer()
        return
    pause = callback_data.arg == "p"
    try:
        await service.set_paused(callback_data.ref, pause)
        text, markup = await _card_view(service, callback_data.ref, settings, store)
    except AppError as exc:
        await call.answer(f"Не получилось: {exc}"[:190], show_alert=True)
        return
    await call.answer("⏸ На паузе" if pause else "▶️ Слежка возобновлена")
    await edit_or_send(call, text, markup)


@router.callback_query(SitesCb.filter(F.action == SitesAction.EVERY))
async def cb_every(call: CallbackQuery, callback_data: SitesCb, apps: Apps, settings: Settings, store: StateStore) -> None:
    service = apps.sites
    if service is None:
        await call.answer()
        return
    try:
        site: Site = await service.get(callback_data.ref)
        new = next_choice(INTERVAL_CHOICES, site.interval_min)
        await service.set_interval(callback_data.ref, new)
        text, markup = await _card_view(service, callback_data.ref, settings, store)
    except AppError as exc:
        await call.answer(f"Не получилось: {exc}"[:190], show_alert=True)
        return
    await call.answer("Частота: как в changedetection" if not new else "Частота изменена")
    await edit_or_send(call, text, markup)


@router.callback_query(SitesCb.filter(F.action == SitesAction.DELETE))
async def cb_delete(call: CallbackQuery, callback_data: SitesCb, apps: Apps, settings: Settings) -> None:
    service = apps.sites
    if service is None:
        await call.answer()
        return
    try:
        site = await service.get(callback_data.ref)
    except AppError:
        await call.answer("Этой слежки уже нет — обновляю")
        await _show_list(call, apps, settings)
        return
    if not callback_data.go:
        await call.answer()
        await edit_or_send(call, render_delete_confirm(site), kb.site_confirm_kb(site.uuid, int(time.time())))
        return
    if not is_fresh(callback_data.ts, time.time()):
        await call.answer("Подтверждение устарело — повторите", show_alert=True)
        return
    try:
        await service.delete(site.uuid)
    except AppError as exc:
        await call.answer(f"Не получилось: {exc}"[:190], show_alert=True)
        return
    await call.answer("🗑 Удалено")
    await _show_list(call, apps, settings)


@router.callback_query(SitesCb.filter(F.action == SitesAction.BROWSER))
async def cb_browser(call: CallbackQuery, callback_data: SitesCb, apps: Apps, settings: Settings, store: StateStore) -> None:
    service = apps.sites
    if service is None:
        await call.answer()
        return
    on = callback_data.arg == "1"
    try:
        await service.set_browser(callback_data.ref, on)
        await service.recheck(callback_data.ref)
        text, markup = await _card_view(service, callback_data.ref, settings, store)
    except AppError as exc:
        await call.answer(f"Не получилось: {exc}"[:190], show_alert=True)
        return
    await call.answer("🌐 Открываю браузером — проверяю заново" if on else "Обычные запросы вместо браузера")
    await edit_or_send(call, text, markup)


@router.callback_query(SitesCb.filter(F.action == SitesAction.CHART))
async def cb_chart(call: CallbackQuery, callback_data: SitesCb, apps: Apps, settings: Settings, store: StateStore, bot: Bot) -> None:
    service = apps.sites
    if service is None or not isinstance(call.message, Message):
        await call.answer()
        return
    try:
        site = await service.get(callback_data.ref)
        series = await service.price_history(callback_data.ref)
    except AppError as exc:
        await call.answer(f"Не получилось: {exc}"[:190], show_alert=True)
        return
    if not series:
        await call.answer("Цен пока нет — график появится после первой проверки", show_alert=True)
        return
    await call.answer("📈 Рисую…")
    points = [(datetime.fromtimestamp(ts, settings.tz), price) for ts, price in series]
    async with ChatActionSender.upload_photo(bot=bot, chat_id=call.message.chat.id):
        try:
            png = await asyncio.to_thread(
                render_price_chart,
                points,
                tz=settings.tz,
                title=site.name[:60],
                currency=settings.sites_currency,
                target=target_of(store, site.uuid),
                until=datetime.now(settings.tz),
            )
        except NoDataError:
            await call.answer("Данных пока мало", show_alert=True)
            return
    await call.message.answer_photo(BufferedInputFile(png, "price.png"), reply_markup=kb.sites_alert_kb(site))


@router.callback_query(SitesCb.filter(F.action == SitesAction.TARGET))
async def cb_target(call: CallbackQuery, callback_data: SitesCb, state: FSMContext, apps: Apps) -> None:
    if apps.sites is None:
        await call.answer()
        return
    await call.answer()
    await state.set_state(SitesStates.target)
    await state.update_data(uuid=callback_data.ref)
    await edit_or_send(call, TARGET_PROMPT, kb.sites_cancel_kb())


@router.message(SitesStates.target, ~F.text.startswith("/"))
async def on_target(message: Message, state: FSMContext, apps: Apps, settings: Settings, store: StateStore) -> None:
    service = apps.sites
    data = await state.get_data()
    uuid = data.get("uuid")
    if service is None or not isinstance(uuid, str):
        await state.clear()
        return
    price = parse_price(message.text or "")
    if price is None:
        await message.answer(BAD_PRICE, reply_markup=kb.sites_cancel_kb())
        return
    await state.clear()
    try:
        site = await service.details(uuid)
        below = await set_target(store, uuid, price, current=site.price)
        text, markup = await _card_view(service, uuid, settings, store)
    except AppError as exc:
        await message.answer(f"Не получилось: {exc}")
        return
    note = f"🎯 Цель: до {money(price, settings.sites_currency)}. "
    note += "Сейчас цена уже ниже — сообщу, когда она снова опустится после роста." if below else "Сообщу, когда цена до неё дойдёт."
    await message.answer(note + "\n\n" + text, reply_markup=markup)


@router.callback_query(SitesCb.filter(F.action == SitesAction.TARGET_OFF))
async def cb_target_off(call: CallbackQuery, callback_data: SitesCb, apps: Apps, settings: Settings, store: StateStore) -> None:
    service = apps.sites
    if service is None:
        await call.answer()
        return
    await set_target(store, callback_data.ref, None)
    await call.answer("Цель убрана")
    try:
        text, markup = await _card_view(service, callback_data.ref, settings, store)
    except AppError as exc:
        await call.answer(f"Не получилось: {exc}"[:190], show_alert=True)
        return
    await edit_or_send(call, text, markup)
