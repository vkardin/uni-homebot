"""Сборка диспетчера aiogram: middleware, роутеры, хуки запуска и остановки."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, MenuButtonCommands, MenuButtonWebApp, WebAppInfo

from homebot import __version__
from homebot.changelog import whats_new
from homebot.config import LOAD_PROBLEMS, Settings
from homebot.handlers import build_routers
from homebot.middlewares import AdminOnlyMiddleware, UsageMiddleware
from homebot.services import selfupdate, tiles
from homebot.services.access import AccessService
from homebot.services.apps import Apps
from homebot.services.state import StateStore
from homebot.services.tasks import TaskManager
from homebot.ui import keyboards as kb
from homebot.ui.style import BOT_DESCRIPTION, BOT_SHORT_DESCRIPTION
from homebot.ui.update_texts import render_result

log = logging.getLogger(__name__)

# Подсказки в меню «/» — только то, чем пользуются каждый день. Остальные команды (/charts, /logs, /docker,
# /speed, /torrents, /security, /cleanup, /actual …) работают как раньше, а всё это есть и в кнопках /menu.
COMMANDS = [
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="news", description="Новости: свежие и настройки"),
    BotCommand(command="movies", description="Кино и сериалы: найти, скачать, на ТВ"),
    BotCommand(command="money", description="Финансы: счета, бюджет, операции"),
    BotCommand(command="spend", description="Записать трату: /spend кофе 85"),
    BotCommand(command="p2p", description="P2P-курс Binance: продать USDT за гривну"),
    BotCommand(command="app", description="Веб-панель UNI"),
    BotCommand(command="sites", description="Слежка за ценами и страницами"),
    BotCommand(command="remind", description="Новое напоминание"),
    BotCommand(command="note", description="Новая заметка"),
    BotCommand(command="tools", description="Конвертер файлов и инструменты"),
    BotCommand(command="server", description="Сервер: состояние, сеть, приложения"),
    BotCommand(command="status", description="Состояние сервера"),
    BotCommand(command="setup", description="Начало работы: что ещё настроить"),
    BotCommand(command="doctor", description="Самопроверка: всё ли подключено и работает"),
    BotCommand(command="repair", description="Ремонт сервера по PIN (если включён)"),
    BotCommand(command="tasks", description="Фоновые задачи"),
    BotCommand(command="settings", description="Настройки UNI"),
    BotCommand(command="export", description="Перенос настроек на другой сервер"),
    BotCommand(command="cancel", description="Выйти из текущего режима"),
]

GUEST_COMMANDS = [
    BotCommand(command="menu", description="Меню"),
    BotCommand(command="note", description="Новая заметка"),
    BotCommand(command="remind", description="Новое напоминание"),
    BotCommand(command="p2p", description="Курс P2P"),
    BotCommand(command="news", description="Новости"),
    BotCommand(command="cancel", description="Выйти из текущего режима"),
]


def build_dispatcher(*, admin_id: int, access: AccessService | None = None, **workflow_data: Any) -> Dispatcher:
    """Собирает диспетчер.

    `workflow_data` (settings, system, units, ollama, tasks, ...) попадает в обработчики
    через dependency injection aiogram: обработчик просто объявляет параметр с таким именем.
    """
    dp = Dispatcher(storage=MemoryStorage(), access=access, **workflow_data)

    # Outer-middleware на уровне Update срабатывает раньше любого фильтра и обработчика:
    # чужие апдейты не доходят даже до FSM и роутеров.
    dp.update.outer_middleware(AdminOnlyMiddleware(admin_id, access))

    dp.callback_query.outer_middleware(UsageMiddleware())  # считает только админа: гостям store не нужен, у них свои кнопки

    for router in build_routers():
        dp.include_router(router)

    dp.startup.register(_on_startup)
    dp.shutdown.register(_on_shutdown)
    return dp


KEY_STATUS_MESSAGE = "status.message_id"
KEY_VERSION = "app.version"  # версия при прошлом запуске — чтобы один раз показать «что нового»


async def _status(bot: Bot, settings: Settings, store: StateStore, text: str, *, markup: Any = None) -> None:
    """Сообщение о запуске/перезапуске — одно на всё время: обновляется на месте, а не плодит новые."""
    message_id = store.get(KEY_STATUS_MESSAGE)
    if isinstance(message_id, int):
        try:
            await bot.edit_message_text(text=text, chat_id=settings.admin_id, message_id=message_id, reply_markup=markup)
            return
        except TelegramAPIError as exc:  # удалено, слишком старое и т. п.
            if "not modified" in str(exc).lower():
                return
            log.debug("Status message cannot be edited: %s", exc)
    sent = await bot.send_message(settings.admin_id, text, reply_markup=markup)
    await store.set(KEY_STATUS_MESSAGE, sent.message_id)


_OFF_COMMANDS: set[str] = set()  # команды выключенных модулей-разделов


def set_off_commands(commands: set[str]) -> None:
    _OFF_COMMANDS.clear()
    _OFF_COMMANDS.update(commands)


def visible_commands(commands: list[BotCommand], connected: set[str]) -> list[BotCommand]:
    """Список команд без команд неподключённых плиток и выключенных разделов."""
    hidden = tiles.hidden_commands(connected) | _OFF_COMMANDS
    return [c for c in commands if c.command not in hidden]


def sync_modules(store: StateStore) -> None:
    """Меню и подсказки команд — по списку выключенных модулей-разделов."""
    from homebot.services import modules

    ids = modules.off(store)
    set_off_commands(modules.hidden_commands(ids))
    kb.set_sections_off(modules.hidden_menu(ids))


async def _on_startup(bot: Bot, settings: Settings, tasks: TaskManager, store: StateStore, apps: Apps | None = None) -> None:
    sections = tiles.connected_ids(apps) if apps is not None else set(tiles.BY_ID)
    sync_modules(store)
    kb.set_connected_tiles(sections)
    with suppress(TelegramAPIError):  # у гостей по умолчанию короткий список, у вас в чате — полный
        await bot.set_my_commands(visible_commands(GUEST_COMMANDS, sections))
        await bot.set_my_commands(visible_commands(COMMANDS, sections), scope=BotCommandScopeChat(chat_id=settings.admin_id))
    with suppress(TelegramAPIError):  # описание в пустом чате и в профиле бота
        await bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
        await bot.set_my_description(BOT_DESCRIPTION)

    kb.set_panel_url(settings.web_url if settings.web_enabled else "")
    if settings.web_enabled and settings.web_url:
        button = MenuButtonWebApp(text="Панель", web_app=WebAppInfo(url=settings.web_url))
        # «Панель» — только в вашем чате; у остальных (по умолчанию) обычная кнопка команд, панель им всё равно закрыта
        for chat_id, menu_button in ((settings.admin_id, button), (None, MenuButtonCommands())):
            try:
                await bot.set_chat_menu_button(chat_id=chat_id, menu_button=menu_button)
            except TelegramAPIError as exc:
                log.warning("Не получилось поставить кнопку меню (chat_id=%s): %s", chat_id, exc)
        log.info("Кнопка «Панель» → %s", settings.web_url)

    await tasks.start_enabled()
    with suppress(OSError):  # «бот поднялся» — по этому файлу обновлятор понимает, что новая версия завелась
        selfupdate.write_ready(settings.state_dir, __version__)

    now = datetime.now(settings.tz)
    try:
        previous = store.get(KEY_VERSION) or ("до 3.0" if store.get(KEY_STATUS_MESSAGE) is not None else None)  # бот уже работал
        news = whats_new(previous, __version__)
        if LOAD_PROBLEMS:
            news += (
                f"\n\n⚠️ В .env ошибка в {', '.join(LOAD_PROBLEMS)} — взял значения по умолчанию. Подробности: journalctl -u homebot"
            )
        await _status(bot, settings, store, f"⚡ <b>UNI в строю</b> · v{__version__}\nЗапущен {now:%d.%m в %H:%M}. Меню: /menu{news}")
        await store.set(KEY_VERSION, __version__)
    except TelegramAPIError as exc:
        # Например, админ ещё ни разу не писал боту — это не повод падать.
        log.warning("Cannot send startup notification: %s", exc)
    _spawn_report(bot, settings)


REPORT_WAIT_SEC = 150  # обновлятор пишет итог только после «готов» + проверки, что бот не падает
_report_tasks: set[asyncio.Task[None]] = set()


def _spawn_report(bot: Bot, settings: Settings) -> None:
    task = asyncio.create_task(_report_update(bot, settings))
    _report_tasks.add(task)
    task.add_done_callback(_report_tasks.discard)


async def _report_update(bot: Bot, settings: Settings, *, wait: float = REPORT_WAIT_SEC, step: float = 2.0) -> None:
    """Итог обновления из чата (его кладёт обновлятор) — присылаем один раз после запуска.

    Бот стартует раньше, чем обновлятор допишет result.json: пока обновлятор держит lock, ждём файл в фоне.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    result = selfupdate.read_result(settings.state_dir)
    while result is None and loop.time() < deadline and selfupdate.updater_busy(settings.state_dir):
        await asyncio.sleep(step)
        result = selfupdate.read_result(settings.state_dir)
    if result is None:
        return
    try:
        await bot.send_message(settings.admin_id, render_result(result))
    except TelegramAPIError as exc:
        log.warning("Cannot send update result: %s", exc)  # файл остаётся — отправим при следующем запуске
        return
    selfupdate.clear_result(settings.state_dir)


async def _on_shutdown(bot: Bot, settings: Settings, tasks: TaskManager, store: StateStore) -> None:
    from homebot.handlers.apps import stop_background_tasks
    from homebot.handlers.monitoring import stop_all_live

    await stop_all_live()
    for task in list(_report_tasks):
        task.cancel()
    await stop_background_tasks()
    await tasks.shutdown()
    now = datetime.now(settings.tz)
    with suppress(TelegramAPIError, TimeoutError):
        # Таймаут короткий: при выключении сервера сети уже может не быть, а systemd ждать не будет.
        await asyncio.wait_for(_status(bot, settings, store, f"💤 <b>UNI перезагружается…</b> · {now:%d.%m %H:%M}"), timeout=5)
