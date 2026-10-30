"""🧩 Приложения: подключение и отключение прямо из чата (поиск контейнеров, вопросы, проверка связи)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

import aiohttp
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommandScopeChat, CallbackQuery, InlineKeyboardMarkup, Message

from homebot.config import Settings
from homebot.services import catalog, connect, modules, tiles
from homebot.services.apps import Apps, build_apps
from homebot.services.doctor import short_error
from homebot.services.state import StateStore
from homebot.services.tiles import CONNECTOR_BY_SERVICE, CONNECTORS, Connector
from homebot.states import TileStates
from homebot.ui import keyboards as kb
from homebot.ui.access_texts import esc
from homebot.ui.callbacks import TileAction, TileCb
from homebot.utils.telegram import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="tiles")

SCAN_TIMEOUT = 8
PROBE_TIMEOUT = 20


async def _scan(apps: Apps) -> dict[str, connect.Found] | None:
    """Найденные контейнеры по сервису; None — искать негде (Docker не подключён или не ответил)."""
    if apps.docker is None:
        return None
    try:
        raw = await asyncio.wait_for(apps.docker.containers_raw(), SCAN_TIMEOUT)
    except Exception as exc:  # сеть, прокси Docker, таймаут
        log.info("Container scan failed: %s", exc)
        return None
    return {f.service: f for f in connect.match_containers(raw)}


async def _overview(settings: Settings, apps: Apps, off: frozenset[str] = frozenset()) -> tuple[str, InlineKeyboardMarkup]:
    """off — сервисы выключенных модулей."""
    found = await _scan(apps)
    lines = ["🧩 <b>Приложения</b>", "Подключайте прямо отсюда: адрес и ключ я спрошу, связь проверю сам.", ""]
    rows: list[tuple[str, str]] = []
    for c in CONNECTORS:
        on = getattr(apps, c.service, None) is not None
        if c.service in off:
            lines.append(f"⏸ {c.title} — модуль выключен (🧱 Модули)")
        elif connect.explicit(settings, c.service):
            lines.append(f"✅ {c.title} — задан в .env" if on else f"⚠️ {c.title} — в .env, но не хватает параметров")
        elif on:
            lines.append(f"✅ {c.title} — подключён из чата")
            rows.append((c.service, f"✅ {c.title}"))
        elif found and c.service in found:
            lines.append(f"🔍 {c.title} — нашёл контейнер «{esc(found[c.service].container)}»")
            rows.append((c.service, f"➕ {c.title} (найден)"))
        else:
            lines.append(f"➕ {c.title}")
            rows.append((c.service, f"➕ {c.title}"))
    if found is None:
        lines += ["", "<i>Docker не подключён, поэтому контейнеры не ищу.</i>"]
    return "\n".join(lines), kb.tiles_kb(rows, catalog=settings.catalog_enabled and catalog.available(settings.state_dir))


async def _refresh(bot: Bot, settings: Settings, apps: Apps) -> None:
    """Меню и подсказки команд — по новому списку подключённого."""
    from homebot.app import COMMANDS, GUEST_COMMANDS, visible_commands  # app импортирует роутеры

    connected = tiles.connected_ids(apps)
    kb.set_connected_tiles(connected)
    with suppress(TelegramAPIError):
        await bot.set_my_commands(visible_commands(GUEST_COMMANDS, connected))
        await bot.set_my_commands(visible_commands(COMMANDS, connected), scope=BotCommandScopeChat(chat_id=settings.admin_id))


def _steps(c: Connector) -> list[str]:
    return ([c.url_field] if c.url_field else []) + [f.name for f in c.fields]


def _prompt(c: Connector, step: str, guess: str) -> str:
    if step == c.url_field:
        return f"🔌 <b>{c.title}</b>\nАдрес? Пришлите ссылку или «+», чтобы взять <code>{esc(guess)}</code>."
    field = next(f for f in c.fields if f.name == step)
    tail = "\nСообщение с ним я сразу удалю из чата." if field.secret else ""
    return f"🔌 <b>{c.title}</b>\nПришлите {esc(field.label)}.{tail}"


@router.message(Command("connect"))
async def cmd_connect(message: Message, settings: Settings, apps: Apps, store: StateStore, state: FSMContext) -> None:
    await state.clear()
    text, markup = await _overview(settings, apps, modules.off_services(store))
    await message.answer(text, reply_markup=markup)


@router.callback_query(TileCb.filter(F.action == TileAction.LIST))
async def cb_list(call: CallbackQuery, settings: Settings, apps: Apps, store: StateStore, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    text, markup = await _overview(settings, apps, modules.off_services(store))
    await edit_or_send(call, text, markup)


@router.callback_query(TileCb.filter(F.action == TileAction.OPEN))
async def cb_open(call: CallbackQuery, callback_data: TileCb, settings: Settings, apps: Apps) -> None:
    c = CONNECTOR_BY_SERVICE.get(callback_data.svc)
    if c is None or connect.explicit(settings, c.service):
        await call.answer("Это приложение настроено в .env", show_alert=True)
        return
    await call.answer()
    if getattr(apps, c.service, None) is not None:
        await edit_or_send(call, f"✅ <b>{c.title}</b> подключён из чата.", kb.tile_kb(c.service, connected=True))
        return
    asks = ", ".join(f.label.split(":")[0] for f in c.fields)
    text = f"➕ <b>{c.title}</b>\nСпрошу: {'адрес, ' if c.url_field else ''}{esc(asks)}. Потом проверю связь."
    await edit_or_send(call, text, kb.tile_kb(c.service, connected=False))


@router.callback_query(TileCb.filter(F.action == TileAction.CONNECT))
async def cb_connect(call: CallbackQuery, callback_data: TileCb, settings: Settings, apps: Apps, state: FSMContext) -> None:
    c = CONNECTOR_BY_SERVICE.get(callback_data.svc)
    if c is None or connect.explicit(settings, c.service):
        await call.answer("Это приложение настроено в .env", show_alert=True)
        return
    await call.answer()
    found = (await _scan(apps) or {}).get(c.service) if c.url_field else None
    guess = found.url if found else f"http://127.0.0.1:{c.port}"
    steps = _steps(c)
    await state.set_state(TileStates.field)
    await state.update_data(svc=c.service, idx=0, values={}, guess=guess)
    await edit_or_send(call, _prompt(c, steps[0], guess), kb.tile_cancel_kb())


@router.message(TileStates.field, F.text, ~F.text.startswith("/"))
async def on_field(message: Message, bot: Bot, settings: Settings, apps: Apps, http: aiohttp.ClientSession, state: FSMContext) -> None:
    data = await state.get_data()
    c = CONNECTOR_BY_SERVICE.get(data.get("svc", ""))
    if c is None:
        await state.clear()
        return
    steps = _steps(c)
    idx: int = data.get("idx", 0)
    step = steps[idx]
    text = (message.text or "").strip()
    if any(f.name == step and f.secret for f in c.fields):
        with suppress(TelegramAPIError):
            await message.delete()
    if step == c.url_field:
        value = data["guess"] if text == "+" else connect.normalize_url(text)
        if value is None:
            await message.answer(
                "Не похоже на адрес. Нужно вида <code>http://127.0.0.1:7878</code> или «+».", reply_markup=kb.tile_cancel_kb()
            )
            return
    else:
        value = text
        if not value:
            return
    values = {**data.get("values", {}), step: value}
    if idx + 1 < len(steps):
        await state.update_data(idx=idx + 1, values=values)
        await message.answer(_prompt(c, steps[idx + 1], data["guess"]), reply_markup=kb.tile_cancel_kb())
        return
    await state.clear()
    await _finish(message, bot, settings, apps, http, c, values)


async def _finish(
    message: Message, bot: Bot, settings: Settings, apps: Apps, http: aiohttp.ClientSession, c: Connector, values: dict[str, str]
) -> None:
    wait = await message.answer(f"🔌 Проверяю связь с {c.title}…")
    candidate = build_apps(http, connect.apply(settings, {c.service: values}))
    try:
        detail = await asyncio.wait_for(connect.probe(candidate, c.service), PROBE_TIMEOUT)
    except Exception as exc:  # любые ошибки приложения или сети показываем человеку одной строкой
        await wait.edit_text(
            f"❌ <b>{c.title}</b> не отвечает: {esc(short_error(exc))}\nПроверьте адрес и ключ и попробуйте ещё раз.",
            reply_markup=kb.tile_kb(c.service, connected=False),
        )
        return
    setattr(apps, c.service, getattr(candidate, c.service))
    stored = connect.load(settings.state_dir)
    stored[c.service] = values
    connect.save(settings.state_dir, stored)
    await _refresh(bot, settings, apps)
    await wait.edit_text(
        f"✅ <b>{c.title}</b> подключён: {esc(detail)}.\nКнопки и команды уже на месте; фоновые уведомления о нём "
        "включатся после перезапуска бота.",
        reply_markup=kb.tile_kb(c.service, connected=True),
    )


@router.callback_query(TileCb.filter(F.action == TileAction.FORGET))
async def cb_forget(call: CallbackQuery, callback_data: TileCb, bot: Bot, settings: Settings, apps: Apps, store: StateStore) -> None:
    c = CONNECTOR_BY_SERVICE.get(callback_data.svc)
    if c is None or connect.explicit(settings, c.service):
        await call.answer("Это приложение настроено в .env", show_alert=True)
        return
    connect.save(settings.state_dir, connect.forget(connect.load(settings.state_dir), c.service))
    setattr(apps, c.service, None)
    await _refresh(bot, settings, apps)
    await call.answer(f"{c.title} отключён")
    text, markup = await _overview(settings, apps, modules.off_services(store))
    await edit_or_send(call, text, markup)


# ------------------------------------------------------------------ 📦 каталог
CATALOG_WAIT = 25 * 60
_installs: set[asyncio.Task[None]] = set()


@router.callback_query(TileCb.filter(F.action == TileAction.CATALOG))
async def cb_catalog(call: CallbackQuery, settings: Settings, apps: Apps) -> None:
    if not (settings.catalog_enabled and catalog.available(settings.state_dir)):
        await call.answer("Каталог выключен: CATALOG_ENABLED=true в .env и update.sh", show_alert=True)
        return
    await call.answer()
    items = [i for i in catalog.ITEMS if getattr(apps, i.service, None) is None]
    lines = ["📦 <b>Каталог</b>", "Поставлю контейнер и сам подключу его. Установка занимает от минуты до нескольких.", ""]
    lines += [f"• <b>{i.title}</b> — {i.about}" for i in items] or ["Всё из каталога уже подключено."]
    await edit_or_send(call, "\n".join(lines), kb.catalog_kb([(i.name, f"⬇️ {i.title}") for i in items]))


@router.callback_query(TileCb.filter(F.action == TileAction.INSTALL))
async def cb_install(
    call: CallbackQuery, callback_data: TileCb, bot: Bot, settings: Settings, apps: Apps, http: aiohttp.ClientSession
) -> None:
    item = catalog.BY_NAME.get(callback_data.svc)
    if item is None or not (settings.catalog_enabled and catalog.available(settings.state_dir)):
        await call.answer("Каталог выключен", show_alert=True)
        return
    if catalog.busy(settings.state_dir):
        await call.answer("Уже что-то ставлю, подождите", show_alert=True)
        return
    catalog.request(settings.state_dir, item.name)
    await call.answer()
    await edit_or_send(call, f"⬇️ Ставлю <b>{item.title}</b>… Напишу, когда закончу.", None)
    task = asyncio.create_task(_wait_install(bot, settings, apps, http, item))
    _installs.add(task)
    task.add_done_callback(_installs.discard)


async def _wait_install(bot: Bot, settings: Settings, apps: Apps, http: aiohttp.ClientSession, item: catalog.Item) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CATALOG_WAIT
    result = None
    while loop.time() < deadline:
        await asyncio.sleep(5)
        result = catalog.result(settings.state_dir)
        if result is not None and result.get("app") == item.name:
            break
        result = None
    if result is None:
        await bot.send_message(
            settings.admin_id, f"⌛ {item.title}: установка не ответила за 25 минут. Посмотрите journalctl -u homebot-catalog"
        )
        return
    if not result.get("ok"):
        await bot.send_message(settings.admin_id, f"❌ {item.title} не установлен: {esc(str(result.get('message', '')))}")
        return
    text = f"✅ <b>{item.title}</b> установлен: {esc(str(result.get('url', '')))}"
    values = connect.load(settings.state_dir).get(item.service)
    if values:  # автонастройка нашла ключ — подключаем сразу
        candidate = build_apps(http, connect.apply(settings, {item.service: values}))
        try:
            await asyncio.wait_for(connect.probe(candidate, item.service), PROBE_TIMEOUT)
        except Exception as exc:  # приложение ещё запускается или ключ не подошёл
            text += f"\nПодключить сразу не вышло ({esc(short_error(exc))}) — попробуйте в 🧩 Приложениях через минуту."
        else:
            setattr(apps, item.service, getattr(candidate, item.service))
            await _refresh(bot, settings, apps)
            text += "\nУже подключён: кнопки на месте."
    elif item.after:
        text += "\n" + item.after
    else:
        text += "\nПодключите его в 🧩 Приложениях."
    await bot.send_message(
        settings.admin_id, text, reply_markup=kb.tile_kb(item.service, connected=getattr(apps, item.service) is not None)
    )


# ------------------------------------------------------------------ 🧱 модули
def _modules_view(apps: Apps, store: StateStore) -> tuple[str, InlineKeyboardMarkup]:
    rows = modules.rows(apps, modules.off(store))
    text = "🧱 <b>Модули</b>\nНажмите, чтобы выключить или включить. Настройки при этом не теряются."
    if not rows:
        text += "\n\n<i>Пока ничего не подключено.</i>"
    return text, kb.modules_kb(rows)


@router.callback_query(TileCb.filter(F.action == TileAction.MODULES))
async def cb_modules(call: CallbackQuery, apps: Apps, store: StateStore, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    text, markup = _modules_view(apps, store)
    await edit_or_send(call, text, markup)


@router.callback_query(TileCb.filter(F.action == TileAction.TOGGLE))
async def cb_toggle(
    call: CallbackQuery,
    callback_data: TileCb,
    bot: Bot,
    settings: Settings,
    apps: Apps,
    http: aiohttp.ClientSession,
    store: StateStore,
) -> None:
    tile = tiles.BY_ID.get(callback_data.svc)
    if tile is None:
        await call.answer()
        return
    if tile.id in modules.off(store):
        await modules.set_off(store, tile.id, False)
        modules.restore(apps, build_apps(http, connect.apply(settings, connect.load(settings.state_dir))), tile.id)
        note = f"{tile.title} включён; фоновые уведомления — после перезапуска"
    else:
        await modules.set_off(store, tile.id, True)
        modules.apply(apps, {tile.id})
        note = f"{tile.title} выключен; фоновые уведомления остановятся после перезапуска"
    await _refresh(bot, settings, apps)
    await call.answer(note, show_alert=True)
    text, markup = _modules_view(apps, store)
    await edit_or_send(call, text, markup)
