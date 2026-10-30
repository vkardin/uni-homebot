"""Inline-клавиатуры."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import tzinfo
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from homebot.services.access import FEATURES, STATUS_ICON, Person
from homebot.services.backup import BackupFile
from homebot.services.cleanup import MAX_SHOWN, Watched, signature
from homebot.services.db import Note, Reminder
from homebot.services.docker_api import Container
from homebot.services.history import PERIODS
from homebot.services.movie_picks import MOODS
from homebot.services.news import feed_name
from homebot.services.prefs import NewsPrefs, NightPrefs, P2PPrefs
from homebot.services.presets import PRESET_KEYS, PRESETS
from homebot.services.radarr import Film
from homebot.services.sites import SITES_SHOWN, Site
from homebot.services.sonarr import Show
from homebot.services.tasks import TaskInfo
from homebot.services.tiles import MENU_TILES
from homebot.ui.backup_texts import when as backup_when
from homebot.ui.callbacks import (
    AccessAction,
    AccessCb,
    App,
    AppAction,
    AppCb,
    BackupAction,
    BackupCb,
    ChartCb,
    CleanAction,
    CleanCb,
    ExpenseAction,
    ExpenseCb,
    ItemAction,
    LogAction,
    LogCb,
    MenuAction,
    MenuCb,
    MovieAction,
    MovieCb,
    NewsAction,
    NewsCb,
    NoteCb,
    OllamaAction,
    OllamaCb,
    P2PAction,
    P2PCb,
    PowerAction,
    PowerCb,
    ReminderCb,
    RepairCb,
    SetAction,
    SetCb,
    SetSection,
    SitesAction,
    SitesCb,
    TaskCb,
    TileAction,
    TileCb,
    ToolAction,
    ToolCb,
    UpdateAction,
    UpdateCb,
    VoiceAction,
    VoiceCb,
)
from homebot.utils.formatting import truncate

_PERIOD_LABELS = {"6h": "6 ч", "24h": "24 ч", "7d": "7 дн"}


def _menu(action: MenuAction) -> MenuCb:
    return MenuCb(action=action)


def _ollama(action: OllamaAction, idx: int = -1) -> OllamaCb:
    return OllamaCb(action=action, idx=idx)


def _back(builder: InlineKeyboardBuilder, action: MenuAction = MenuAction.HOME, text: str = "⬅️ Меню") -> None:
    builder.button(text=text, callback_data=_menu(action))


_PANEL_URL = ""


def set_panel_url(url: str) -> None:
    """Адрес веб-панели: если задан (https), в главном меню появляется большая кнопка «Панель»."""
    global _PANEL_URL
    _PANEL_URL = url if url.startswith("https://") else ""


# Разделы меню плиток (services/tiles.py): кнопка есть, только если плитка подключена.
TILE_SECTIONS = {"movies": MenuAction.MOVIES, "finance": MenuAction.FINANCE, "p2p": MenuAction.P2P, "sites": MenuAction.SITES}
_HIDDEN: set[MenuAction] = set()
_OFF_SECTIONS: set[MenuAction] = set()  # 🧱 выключенные встроенные разделы (новости, напоминания…)
_LAST_CONNECTED: set[str] = set()


def set_connected_tiles(connected: set[str]) -> None:
    """Какие плитки подключены: разделы остальных прячутся из главного и гостевого меню."""
    _LAST_CONNECTED.clear()
    _LAST_CONNECTED.update(connected)
    _HIDDEN.clear()
    _HIDDEN.update(TILE_SECTIONS[t.menu] for t in MENU_TILES if t.id not in connected and t.menu in TILE_SECTIONS)
    _HIDDEN.update(_OFF_SECTIONS)


def set_sections_off(names: set[str]) -> None:
    """Выключенные модули-разделы по именам MenuAction (NEWS, TOOLS…): их кнопки пропадают из меню."""
    _OFF_SECTIONS.clear()
    _OFF_SECTIONS.update(MenuAction[n] for n in names if n in MenuAction.__members__)
    set_connected_tiles(set(_LAST_CONNECTED))


def main_menu() -> InlineKeyboardMarkup:
    """Главное меню по приоритету: новости и личное — сверху, сервер — в отдельном разделе."""
    builder = InlineKeyboardBuilder()
    if _PANEL_URL:
        builder.button(text="🖥 Открыть панель", web_app=WebAppInfo(url=_PANEL_URL))
    for text, action in (
        ("🗞 Новости", MenuAction.NEWS),
        ("🎬 Кино", MenuAction.MOVIES),
        ("💰 Финансы", MenuAction.FINANCE),
        ("💱 P2P курс", MenuAction.P2P),
        ("🔎 Сайты", MenuAction.SITES),
        ("🤖 Ассистент", MenuAction.OLLAMA),
        ("⏰ Напоминания", MenuAction.REMINDERS),
        ("📝 Заметки", MenuAction.NOTES),
        ("🧰 Инструменты", MenuAction.TOOLS),
        ("🖥 Сервер", MenuAction.SERVER),
        ("⚙️ Настройки", MenuAction.SETTINGS),
    ):
        if action not in _HIDDEN:
            builder.button(text=text, callback_data=_menu(action))
    builder.adjust(*([1] if _PANEL_URL else []), 2)
    return builder.as_markup()


def server_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for text, action in (
        ("📊 Состояние", MenuAction.STATUS),
        ("📈 Графики", MenuAction.CHARTS),
        ("🧩 Службы", MenuAction.SERVICES),
        ("🌐 Сеть", MenuAction.NETWORK),
        ("🛡 Безопасность", MenuAction.SECURITY),
        ("🧰 Приложения", MenuAction.APPS),
        ("⚡ Управление", MenuAction.POWER),
        ("🩺 Самопроверка", MenuAction.DOCTOR),
    ):
        builder.button(text=text, callback_data=_menu(action))
    _back(builder)
    builder.adjust(2, 2, 2, 2, 1)
    return builder.as_markup()


def _back_server(builder: InlineKeyboardBuilder) -> None:
    _back(builder, MenuAction.SERVER, "⬅️ Сервер")


def back_kb(action: MenuAction = MenuAction.HOME, text: str = "⬅️ Меню") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _back(builder, action, text)
    return builder.as_markup()


# ------------------------------------------------------------------ мониторинг
def status_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data=_menu(MenuAction.STATUS))
    builder.button(text="📡 Live", callback_data=_menu(MenuAction.LIVE))
    builder.button(text="🔝 Топ процессов", callback_data=_menu(MenuAction.TOP))
    builder.button(text="📈 Графики", callback_data=_menu(MenuAction.CHARTS))
    _back_server(builder)
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def live_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⏹ Остановить", callback_data=_menu(MenuAction.LIVE_STOP))
    return builder.as_markup()


def top_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data=_menu(MenuAction.TOP))
    builder.button(text="📊 Состояние", callback_data=_menu(MenuAction.STATUS))
    _back_server(builder)
    builder.adjust(2, 1)
    return builder.as_markup()


def charts_kb(active: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for period in PERIODS:
        label = _PERIOD_LABELS.get(period, period)
        builder.button(text=f"• {label} •" if period == active else label, callback_data=ChartCb(period=period))
    _back_server(builder)
    builder.adjust(len(PERIODS), 1)
    return builder.as_markup()


def services_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data=_menu(MenuAction.SERVICES))
    builder.button(text="📜 Журналы", callback_data=_menu(MenuAction.LOGS))
    _back_server(builder)
    builder.adjust(2, 1)
    return builder.as_markup()


def logs_menu_kb(units: Sequence[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, unit in enumerate(units):
        builder.button(text=f"📜 {unit}", callback_data=LogCb(action=LogAction.SHOW, idx=index))
    _back(builder, MenuAction.SERVICES, "⬅️ Службы")
    builder.adjust(2)
    return builder.as_markup()


def log_view_kb(idx: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data=LogCb(action=LogAction.SHOW, idx=idx))
    builder.button(text="🧠 Объяснить", callback_data=LogCb(action=LogAction.EXPLAIN, idx=idx))
    _back(builder, MenuAction.LOGS, "⬅️ К журналам")
    builder.adjust(2, 1)
    return builder.as_markup()


def refresh_kb(action: MenuAction) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data=_menu(action))
    _back_server(builder)
    builder.adjust(2)
    return builder.as_markup()


# ------------------------------------------------------------------ управление
def power_kb(restartable: Sequence[str], *, power_enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, unit in enumerate(restartable):
        builder.button(text=f"🔁 {unit}", callback_data=PowerCb(action=PowerAction.RESTART, idx=index))
    if power_enabled:
        builder.button(text="♻️ Перезагрузить сервер", callback_data=PowerCb(action=PowerAction.REBOOT))
        builder.button(text="⏻ Выключить сервер", callback_data=PowerCb(action=PowerAction.POWEROFF))
    _back_server(builder)
    builder.adjust(1)
    return builder.as_markup()


def confirm_kb(action: PowerAction, idx: int, ts: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, выполнить", callback_data=PowerCb(action=action, idx=idx, ts=ts, go=1))
    builder.button(text="❌ Отмена", callback_data=_menu(MenuAction.POWER))
    builder.adjust(2)
    return builder.as_markup()


# --------------------------------------------------------------------- фоновые задачи
def tasks_kb(items: Sequence[TaskInfo]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        mark = "🟢 вкл" if item.running else "⚪ выкл"
        builder.button(text=f"{item.title}: {mark}", callback_data=TaskCb(name=item.name))
    builder.button(text="⬅️ Настройки", callback_data=SetCb(sec=SetSection.ROOT))
    builder.adjust(1)
    return builder.as_markup()


# ---------------------------------------------------------------------- Ollama
def ollama_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Чат", callback_data=_ollama(OllamaAction.CHAT))
    builder.button(text="📝 Резюме текста/ссылки", callback_data=_ollama(OllamaAction.SUMMARIZE))
    builder.button(text="🔁 Модель", callback_data=_ollama(OllamaAction.MODELS))
    builder.button(text="🧹 Выгрузить из RAM", callback_data=_ollama(OllamaAction.UNLOAD))
    builder.button(text="🔄 Обновить", callback_data=_ollama(OllamaAction.PANEL))
    _back(builder)
    builder.adjust(2, 2, 2)
    return builder.as_markup()


def chat_kb() -> InlineKeyboardMarkup:
    """Клавиатура под сообщениями в режиме чата/суммаризации."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🧹 Выгрузить из RAM", callback_data=_ollama(OllamaAction.UNLOAD))
    builder.button(text="🚪 Выйти", callback_data=_ollama(OllamaAction.PANEL))
    builder.adjust(2)
    return builder.as_markup()


def models_kb(models: Sequence[str], active: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, name in enumerate(models):
        label = f"✅ {name}" if name == active else name
        builder.button(text=label, callback_data=_ollama(OllamaAction.PICK, index))
    builder.button(text="⬅️ Назад", callback_data=_ollama(OllamaAction.PANEL))
    builder.adjust(1)
    return builder.as_markup()


# ---------------------------------------------------------- напоминания и заметки
def reminders_kb(items: Sequence[Reminder]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        builder.button(text=f"❌ {truncate(item.text, 28)}", callback_data=ReminderCb(action=ItemAction.DELETE, id=item.id))
    builder.button(text="➕ Добавить", callback_data=ReminderCb(action=ItemAction.ADD))
    _back(builder)
    builder.adjust(*([1] * len(items)), 2)
    return builder.as_markup()


def notes_kb(items: Sequence[Note]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        builder.button(text=f"🗑 {truncate(item.text, 28)}", callback_data=NoteCb(action=ItemAction.DELETE, id=item.id))
    builder.button(text="➕ Добавить", callback_data=NoteCb(action=ItemAction.ADD))
    _back(builder)
    builder.adjust(*([1] * len(items)), 2)
    return builder.as_markup()


# ------------------------------------------------------------------- приложения
_APP_TITLES = {
    App.DOCKER: "🐳 Docker",
    App.SPEEDTEST: "🚀 Speedtest",
    App.QBIT: "📥 Торренты",
    App.ADGUARD: "🛡 AdGuard",
    App.JELLYFIN: "🎬 Jellyfin",
}


def _app(app: App, action: AppAction = AppAction.VIEW, arg: str = "", ts: int = 0, go: int = 0) -> AppCb:
    return AppCb(app=app, action=action, arg=arg, ts=ts, go=go)


def _app_back(builder: InlineKeyboardBuilder, app: App) -> None:
    builder.button(text="🔄 Обновить", callback_data=_app(app))
    builder.button(text="⬅️ Приложения", callback_data=_menu(MenuAction.APPS))


def apps_kb(available: Sequence[App]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for app in available:
        builder.button(text=_APP_TITLES[app], callback_data=_app(app))
    _back_server(builder)
    builder.adjust(2)
    return builder.as_markup()


def docker_kb(containers: Sequence[Container]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for c in containers:
        if len(c.name) <= 30:  # лимит callback_data — 64 байта
            builder.button(text=f"{c.emoji} {c.name}", callback_data=_app(App.DOCKER, AppAction.CONTAINER, c.name))
    _app_back(builder, App.DOCKER)
    builder.adjust(2)
    return builder.as_markup()


def container_kb(name: str, *, restart: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data=_app(App.DOCKER, AppAction.CONTAINER, name))
    builder.button(text="🧠 Разобрать журнал", callback_data=_app(App.DOCKER, AppAction.EXPLAIN, name))
    if restart:
        builder.button(text="🔁 Перезапустить", callback_data=_app(App.DOCKER, AppAction.RESTART, name))
    builder.button(text="⬅️ Контейнеры", callback_data=_app(App.DOCKER))
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def container_confirm_kb(name: str, ts: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, перезапустить", callback_data=_app(App.DOCKER, AppAction.RESTART, name, ts, 1))
    builder.button(text="❌ Отмена", callback_data=_app(App.DOCKER, AppAction.CONTAINER, name))
    builder.adjust(2)
    return builder.as_markup()


def docker_alert_kb(names: Sequence[str], restart: bool) -> InlineKeyboardMarkup:
    """Под сообщением о проблеме: по контейнеру — «журнал» и «перезапустить» (с подтверждением)."""
    builder = InlineKeyboardBuilder()
    rows: list[int] = []
    for name in list(names)[:3]:
        builder.button(text=truncate(f"📜 {name}", 30), callback_data=_app(App.DOCKER, AppAction.CONTAINER, name))
        if restart:
            builder.button(text=truncate(f"🔁 {name}", 30), callback_data=_app(App.DOCKER, AppAction.RESTART, name))
            rows.append(2)
        else:
            rows.append(1)
    builder.adjust(*rows)
    return builder.as_markup()


def speedtest_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="▶️ Запустить замер", callback_data=_app(App.SPEEDTEST, AppAction.RUN))
    builder.button(text="📈 График 7 дней", callback_data=_app(App.SPEEDTEST, AppAction.CHART))
    _app_back(builder, App.SPEEDTEST)
    builder.adjust(2, 2)
    return builder.as_markup()


def qbit_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⏸ Всё на паузу", callback_data=_app(App.QBIT, AppAction.PAUSE))
    builder.button(text="▶️ Продолжить всё", callback_data=_app(App.QBIT, AppAction.RESUME))
    _app_back(builder, App.QBIT)
    builder.adjust(2, 2)
    return builder.as_markup()


def adguard_kb(*, protection: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if protection:
        builder.button(text="⏸ Выключить на 10 минут", callback_data=_app(App.ADGUARD, AppAction.OFF))
    else:
        builder.button(text="▶️ Включить защиту", callback_data=_app(App.ADGUARD, AppAction.ON))
    _app_back(builder, App.ADGUARD)
    builder.adjust(1, 2)
    return builder.as_markup()


def app_refresh_kb(app: App) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _app_back(builder, app)
    builder.adjust(2)
    return builder.as_markup()


# -------------------------------------------------------------------- новости
def _on(flag: bool) -> str:
    return "🟢" if flag else "⚪"


def brief_kb() -> InlineKeyboardMarkup:
    """Под сводкой: обновить на месте, настройки, меню."""
    builder = InlineKeyboardBuilder()
    builder.button(text="⚡ Обновить сейчас", callback_data=NewsCb(action=NewsAction.NOW))
    builder.button(text="⚙️ Настройки", callback_data=SetCb(sec=SetSection.NEWS))
    builder.button(text="🏠 Меню", callback_data=_menu(MenuAction.HOME))
    builder.adjust(1, 2)
    return builder.as_markup()


def news_models_kb(models: Sequence[str], current: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=("✅ " if not current else "") + "Как в чате", callback_data=SetCb(sec=SetSection.MODEL, act=SetAction.MODEL, idx=-1)
    )
    for index, name in enumerate(models):
        label = f"✅ {name}" if name == current else name
        builder.button(text=label[:60], callback_data=SetCb(sec=SetSection.MODEL, act=SetAction.MODEL, idx=index))
    builder.button(text="⬅️ Настройки новостей", callback_data=SetCb(sec=SetSection.NEWS))
    builder.adjust(1)
    return builder.as_markup()


def translate_models_kb(models: Sequence[str], current: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=("✅ " if not current else "") + "Как в чате",
        callback_data=SetCb(sec=SetSection.TRANSLATE, act=SetAction.MODEL, idx=-1),
    )
    for index, name in enumerate(models):
        label = f"✅ {name}" if name == current else name
        builder.button(text=label[:60], callback_data=SetCb(sec=SetSection.TRANSLATE, act=SetAction.MODEL, idx=index))
    builder.button(text="⬅️ Настройки", callback_data=SetCb(sec=SetSection.ROOT))
    builder.adjust(1)
    return builder.as_markup()


def news_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⚡ Свежие сейчас", callback_data=NewsCb(action=NewsAction.NOW))
    builder.button(text="⚙️ Настройки новостей", callback_data=SetCb(sec=SetSection.NEWS))
    builder.button(text="📚 Источники", callback_data=SetCb(sec=SetSection.FEEDS))
    _back(builder)
    builder.adjust(1, 2, 1)
    return builder.as_markup()


def settings_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗞 Новости", callback_data=SetCb(sec=SetSection.NEWS))
    builder.button(text="📚 Источники новостей", callback_data=SetCb(sec=SetSection.FEEDS))
    builder.button(text="🔔 Уведомления", callback_data=_menu(MenuAction.TASKS))
    builder.button(text="🤖 Модель ИИ", callback_data=_ollama(OllamaAction.MODELS))
    builder.button(text="🌙 Ночной режим", callback_data=SetCb(sec=SetSection.NIGHT))
    builder.button(text="🌐 Модель перевода", callback_data=SetCb(sec=SetSection.TRANSLATE))
    builder.button(text="⬆️ Обновление", callback_data=UpdateCb(action=UpdateAction.INFO))
    builder.button(text="💾 Бэкап", callback_data=BackupCb(action=BackupAction.HOME))
    builder.button(text="👥 Доступ", callback_data=AccessCb(action=AccessAction.LIST))
    builder.button(text="🧩 Приложения", callback_data=TileCb(action=TileAction.LIST))
    _back(builder)
    builder.adjust(2, 2, 2, 2, 2, 1)
    return builder.as_markup()


def night_kb(night: NightPrefs) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🌙 Включён — выключить" if night.enabled else "🔔 Выключен — включить",
        callback_data=SetCb(sec=SetSection.NIGHT, act=SetAction.ENABLED),
    )
    if night.enabled:
        builder.button(text=f"🕰 {night.label} — сменить", callback_data=SetCb(sec=SetSection.NIGHT, act=SetAction.QUIET_RANGE))
    builder.button(text="⬅️ Настройки", callback_data=SetCb(sec=SetSection.ROOT))
    builder.adjust(1)
    return builder.as_markup()


def news_settings_kb(p: NewsPrefs) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    def s(act: SetAction) -> SetCb:
        return SetCb(sec=SetSection.NEWS, act=act)

    builder.button(text=f"{_on(p.enabled)} Рассылка", callback_data=s(SetAction.ENABLED))
    builder.button(text=f"⏱ {p.interval_label}", callback_data=s(SetAction.INTERVAL))
    builder.button(text=f"🔢 По {p.count} новостей", callback_data=s(SetAction.COUNT))
    builder.button(text=f"{_on(p.quiet)} Тихие часы", callback_data=s(SetAction.QUIET))
    builder.button(text=f"🌙 {p.quiet_from:02d}:00–{p.quiet_to:02d}:00", callback_data=s(SetAction.QUIET_RANGE))
    builder.button(text=f"{_on(p.retell)} Пересказ ИИ", callback_data=s(SetAction.RETELL))
    builder.button(text=f"🤖 Модель: {p.model or 'как в чате'}"[:60], callback_data=SetCb(sec=SetSection.MODEL))
    builder.button(
        text="📌 Одно сообщение на день" if p.mode == "day" else "🔔 Новое сообщение каждый раз", callback_data=s(SetAction.MODE)
    )
    builder.button(text=f"{_on(p.fetch_articles)} Читать статью, если нет описания", callback_data=s(SetAction.ARTICLES))
    builder.button(text="📚 Источники", callback_data=SetCb(sec=SetSection.FEEDS))
    builder.button(text="⬅️ Настройки", callback_data=SetCb(sec=SetSection.ROOT))
    builder.button(text="🗞 К новостям", callback_data=_menu(MenuAction.NEWS))
    builder.adjust(1, 2, 2, 1, 1, 1, 1, 1, 2)
    return builder.as_markup()


def feeds_kb(feeds: Sequence[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, url in enumerate(feeds):
        builder.button(text=f"❌ {feed_name(url)}", callback_data=SetCb(sec=SetSection.FEEDS, act=SetAction.DELETE, idx=index))
    builder.button(text="➕ Добавить ссылку", callback_data=SetCb(sec=SetSection.FEEDS, act=SetAction.ADD))
    builder.button(text="📦 Готовые подборки", callback_data=SetCb(sec=SetSection.PRESETS))
    builder.button(text="⬅️ Настройки новостей", callback_data=SetCb(sec=SetSection.NEWS))
    builder.adjust(*([2] * (len(feeds) // 2)), *([1] if len(feeds) % 2 else []), 2, 1)
    return builder.as_markup()


def presets_kb(active: set[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, key in enumerate(PRESET_KEYS):
        title, urls = PRESETS[key]
        mark = " ✅" if set(urls) <= active else ""
        builder.button(text=f"{title}{mark}", callback_data=SetCb(sec=SetSection.PRESETS, act=SetAction.PRESET, idx=index))
    builder.button(text="⬅️ Источники", callback_data=SetCb(sec=SetSection.FEEDS))
    builder.adjust(2)
    return builder.as_markup()


def cancel_kb(target: SetCb) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=target)
    return builder.as_markup()


# ------------------------------------------------------------------------ кино
def _mv(action: MovieAction, ref: int = 0, arg: str = "") -> MovieCb:
    return MovieCb(action=action, ref=ref, arg=arg)


def _movies_back(builder: InlineKeyboardBuilder) -> None:
    builder.button(text="🎬 Кино", callback_data=_mv(MovieAction.HOME))


def movies_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Найти", callback_data=_mv(MovieAction.SEARCH))
    builder.button(text="🎲 Что посмотреть?", callback_data=_mv(MovieAction.MOODS))
    builder.button(text="🔥 Популярное", callback_data=_mv(MovieAction.TRENDING))
    builder.button(text="🎞 Из скачанного", callback_data=_mv(MovieAction.LIBRARY))
    builder.button(text="🧹 Диск", callback_data=CleanCb(action=CleanAction.VIEW))
    builder.button(text="🔄 Обновить", callback_data=_mv(MovieAction.HOME))
    _back(builder)
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def moods_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, (label, *_) in MOODS.items():
        builder.button(text=label, callback_data=_mv(MovieAction.MOOD, arg=key))
    builder.button(text="✍️ Своими словами", callback_data=_mv(MovieAction.CUSTOM))
    _movies_back(builder)
    builder.adjust(3, 3, 3, 3, 1, 1)
    return builder.as_markup()


def film_icon(film: Film | Show, downloading: bool = False) -> str:
    if isinstance(film, Show):
        if film.complete:
            return "✅"
        if downloading:
            return "⏬"
        return "📺" if not film.in_sonarr else ("🟡" if film.has_file else "⏳")
    if film.has_file:
        return "✅"
    if downloading:
        return "⏬"
    return "⏳" if film.in_radarr else "🎬"


def card_cb(item: Film | Show) -> MovieCb:
    if isinstance(item, Show):
        return _mv(MovieAction.S_CARD, item.tvdb_id)
    return _mv(MovieAction.CARD, item.tmdb_id)


def films_kb(items: Sequence[tuple[MovieCb, str]], *, again: MovieCb | None = None) -> InlineKeyboardMarkup:
    """Список найденного: (кнопка карточки, подпись)."""
    builder = InlineKeyboardBuilder()
    shown = list(items)[:10]
    for data, label in shown:
        builder.button(text=truncate(label, 60), callback_data=data)
    if again is not None:
        builder.button(text="🔁 Ещё варианты", callback_data=again)
    builder.button(text="🔍 Найти", callback_data=_mv(MovieAction.SEARCH))
    _movies_back(builder)
    builder.adjust(*([1] * len(shown)), *([1] if again else []), 2)
    return builder.as_markup()


def film_kb(film: Film, *, playable: bool, resume: bool, downloading: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    top = 0
    if playable:
        if resume:
            builder.button(text="▶️ Продолжить на ТВ", callback_data=_mv(MovieAction.TV, film.tmdb_id, "r"))
            top += 1
        builder.button(text="⏮ С начала" if resume else "📺 Смотреть на ТВ", callback_data=_mv(MovieAction.TV, film.tmdb_id))
        top += 1
    elif not film.in_radarr:
        builder.button(text="⬇️ Скачать", callback_data=_mv(MovieAction.ADD, film.tmdb_id))
        top += 1
    elif not film.has_file and not downloading:
        builder.button(text="🔎 Искать раздачу", callback_data=_mv(MovieAction.RESEARCH, film.tmdb_id))
        top += 1
    builder.button(text="🔄 Обновить", callback_data=_mv(MovieAction.CARD, film.tmdb_id))
    _movies_back(builder)
    builder.adjust(*([top] if top else []), 2)
    return builder.as_markup()


def show_kb(show: Show, *, playable: bool, downloading: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    rows: list[int] = []
    if playable:
        builder.button(text="▶️ Следующая серия на ТВ", callback_data=_mv(MovieAction.S_TV, show.tvdb_id))
        rows.append(1)
    if not show.in_sonarr:
        builder.button(text="⬇️ Все сезоны", callback_data=_mv(MovieAction.S_ADD, show.tvdb_id, "all"))
        builder.button(text="⬇️ Последний сезон", callback_data=_mv(MovieAction.S_ADD, show.tvdb_id, "last"))
        rows.append(2)
    elif not show.complete and not downloading:
        builder.button(text="🔎 Искать серии", callback_data=_mv(MovieAction.S_RESEARCH, show.tvdb_id))
        rows.append(1)
    builder.button(text="🔄 Обновить", callback_data=_mv(MovieAction.S_CARD, show.tvdb_id))
    _movies_back(builder)
    builder.adjust(*rows, 2)
    return builder.as_markup()


def movie_ready_kb(ref: int, *, series: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if series:
        builder.button(text="▶️ Смотреть на ТВ", callback_data=_mv(MovieAction.S_TV, ref))
        builder.button(text="📺 Карточка", callback_data=_mv(MovieAction.S_CARD, ref))
    else:
        builder.button(text="📺 Смотреть на ТВ", callback_data=_mv(MovieAction.TV, ref))
        builder.button(text="🎬 Карточка", callback_data=_mv(MovieAction.CARD, ref))
    builder.adjust(2)
    return builder.as_markup()


def movie_retry_kb(ref: int, *, series: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔎 Искать ещё раз", callback_data=_mv(MovieAction.S_RESEARCH if series else MovieAction.RESEARCH, ref))
    builder.button(text="Карточка", callback_data=_mv(MovieAction.S_CARD if series else MovieAction.CARD, ref))
    builder.adjust(2)
    return builder.as_markup()


def movie_cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✖️ Отмена", callback_data=_mv(MovieAction.HOME))
    return builder.as_markup()


# --------------------------------------------------------------------- чистка диска
def cleanup_kb(items: Sequence[Watched]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    shown = list(items)[:MAX_SHOWN]
    for number, item in enumerate(shown, 1):
        builder.button(
            text=truncate(f"🗑 {number}. {item.label}", 56),
            callback_data=CleanCb(action=CleanAction.DEL, kind=item.kind, ref=item.ref),
        )
    if len(shown) > 1:
        builder.button(
            text=f"🧹 Удалить всё ({len(shown)})",
            callback_data=CleanCb(action=CleanAction.ALL, kind=signature(shown), ref=len(shown)),
        )
    builder.button(text="🔄 Обновить", callback_data=CleanCb(action=CleanAction.VIEW))
    builder.button(text="🎬 Кино", callback_data=_mv(MovieAction.HOME))
    builder.adjust(*([1] * len(shown)), *([1] if len(shown) > 1 else []), 2)
    return builder.as_markup()


def cleanup_confirm_kb(kind: str, ref: int, ts: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=CleanCb(action=CleanAction.DEL, kind=kind, ref=ref, ts=ts, go=1))
    builder.button(text="❌ Отмена", callback_data=CleanCb(action=CleanAction.VIEW))
    builder.adjust(2)
    return builder.as_markup()


def cleanup_all_confirm_kb(count: int, sig: str, ts: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить всё", callback_data=CleanCb(action=CleanAction.ALL, kind=sig, ref=count, ts=ts, go=1))
    builder.button(text="❌ Отмена", callback_data=CleanCb(action=CleanAction.VIEW))
    builder.adjust(2)
    return builder.as_markup()


# --------------------------------------------------------------------- 💰 финансы
def _fin(action: ExpenseAction, draft: int = 0, idx: int = -1) -> ExpenseCb:
    return ExpenseCb(action=action, draft=draft, idx=idx)


MAX_PICK_BUTTONS = 30


def finance_kb(default_account: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➖ Расход", callback_data=_fin(ExpenseAction.NEW_EXPENSE))
    builder.button(text="➕ Доход", callback_data=_fin(ExpenseAction.NEW_INCOME))
    builder.button(text="📋 Операции", callback_data=_fin(ExpenseAction.OPS))
    builder.button(text="📊 Бюджет", callback_data=_fin(ExpenseAction.BUDGET, idx=0))
    builder.button(text=truncate(f"🏦 Записывать на: {default_account}", 40), callback_data=_fin(ExpenseAction.ACCOUNTS))
    builder.button(text="🔄 Обновить", callback_data=_fin(ExpenseAction.HOME))
    builder.button(text="⬅️ Меню", callback_data=_menu(MenuAction.HOME))
    builder.adjust(2, 2, 1, 2)
    return builder.as_markup()


def finance_back_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Финансы", callback_data=_fin(ExpenseAction.HOME))
    return builder.as_markup()


def expense_kb(draft: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Записать", callback_data=_fin(ExpenseAction.SAVE, draft))
    builder.button(text="🏷 Категория", callback_data=_fin(ExpenseAction.CATS, draft))
    builder.button(text="🏦 Счёт", callback_data=_fin(ExpenseAction.DRAFT_ACCOUNTS, draft))
    builder.button(text="✖️ Отмена", callback_data=_fin(ExpenseAction.CANCEL, draft))
    builder.adjust(2, 2)
    return builder.as_markup()


def expense_saved_kb(draft: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="↩️ Отменить", callback_data=_fin(ExpenseAction.UNDO, draft))
    builder.button(text="💰 Финансы", callback_data=_fin(ExpenseAction.HOME))
    builder.adjust(2)
    return builder.as_markup()


def _pick_kb(labels: Sequence[str], action: ExpenseAction, draft: int, back: ExpenseCb) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    shown = list(labels)[:MAX_PICK_BUTTONS]
    for index, label in enumerate(shown):
        builder.button(text=truncate(label, 28), callback_data=_fin(action, draft, index))
    builder.button(text="⬅️ Назад", callback_data=back)
    builder.adjust(*([2] * (len(shown) // 2)), *([1] if len(shown) % 2 else []), 1)
    return builder.as_markup()


def expense_categories_kb(draft: int, names: Sequence[str]) -> InlineKeyboardMarkup:
    return _pick_kb(names, ExpenseAction.PICK, draft, _fin(ExpenseAction.PICK, draft, -1))


def expense_accounts_kb(draft: int, names: Sequence[str]) -> InlineKeyboardMarkup:
    return _pick_kb(names, ExpenseAction.PICK_ACCOUNT, draft, _fin(ExpenseAction.PICK_ACCOUNT, draft, -1))


def default_account_kb(names: Sequence[str]) -> InlineKeyboardMarkup:
    return _pick_kb(names, ExpenseAction.SET_ACCOUNT, 0, _fin(ExpenseAction.HOME))


def entry_cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✖️ Отмена", callback_data=_fin(ExpenseAction.HOME))
    return builder.as_markup()


def ops_kb(token: int, count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index in range(count):
        builder.button(text=f"🗑 {index + 1}", callback_data=_fin(ExpenseAction.DELETE, token, index))
    builder.button(text="🔄 Обновить", callback_data=_fin(ExpenseAction.OPS))
    builder.button(text="⬅️ Финансы", callback_data=_fin(ExpenseAction.HOME))
    builder.adjust(*([5] * (count // 5)), *([count % 5] if count % 5 else []), 2)
    return builder.as_markup()


def delete_confirm_kb(token: int, index: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=_fin(ExpenseAction.DELETE_OK, token, index))
    builder.button(text="❌ Нет", callback_data=_fin(ExpenseAction.OPS))
    builder.adjust(2)
    return builder.as_markup()


def budget_kb(offset: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Раньше", callback_data=_fin(ExpenseAction.BUDGET, idx=offset - 1))
    if offset < 0:
        builder.button(text="Позже ▶️", callback_data=_fin(ExpenseAction.BUDGET, idx=offset + 1))
    builder.button(text="⬅️ Финансы", callback_data=_fin(ExpenseAction.HOME))
    builder.adjust(2 if offset < 0 else 1, 1)
    return builder.as_markup()


def doctor_kb(*, repair: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if repair:
        builder.button(text="🔧 Починить", callback_data=_menu(MenuAction.REPAIR))
    builder.button(text="🔄 Проверить ещё раз", callback_data=_menu(MenuAction.DOCTOR))
    builder.button(text="🛠 Ремонт сервера", callback_data=RepairCb())
    builder.button(text="🖥 Сервер", callback_data=_menu(MenuAction.SERVER))
    builder.button(text="⬅️ Меню", callback_data=_menu(MenuAction.HOME))
    builder.adjust(*([1] if repair else []), 2, 2)
    return builder.as_markup()


def service_alert_kb(failed: Sequence[str], log_units: Sequence[str], restartable: Sequence[str]) -> InlineKeyboardMarkup | None:
    builder = InlineKeyboardBuilder()
    rows: list[int] = []
    for unit in list(failed)[:3]:
        row = 0
        if unit in log_units:
            builder.button(text=truncate(f"📜 {unit}", 30), callback_data=LogCb(action=LogAction.SHOW, idx=log_units.index(unit)))
            row += 1
        if unit in restartable:
            builder.button(
                text=truncate(f"🔁 {unit}", 30), callback_data=PowerCb(action=PowerAction.RESTART, idx=restartable.index(unit))
            )
            row += 1
        if row:
            rows.append(row)
    if not rows:
        return None
    builder.adjust(*rows)
    return builder.as_markup()


# ----------------------------------------------------------------------- P2P-курс
def _p2p(action: P2PAction) -> P2PCb:
    return P2PCb(action=action)


def p2p_kb(prefs: P2PPrefs) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data=_p2p(P2PAction.HOME))
    builder.button(text="📈 График 7 дней", callback_data=_p2p(P2PAction.CHART))
    change = f"±{prefs.threshold:g}%" if prefs.threshold else "выкл"
    builder.button(text=f"🔔 Изменение: {change}", callback_data=_p2p(P2PAction.THRESHOLD))
    target = f"{prefs.target:g}" if prefs.target else "задать"
    builder.button(text=truncate(f"🎯 Цель: {target}", 30), callback_data=_p2p(P2PAction.TARGET))
    builder.button(text=f"🌆 Сводка: {'вкл' if prefs.summary else 'выкл'}", callback_data=_p2p(P2PAction.SUMMARY))
    sizes = [2, 2, 1]
    if prefs.target:
        builder.button(text="❌ Убрать цель", callback_data=_p2p(P2PAction.TARGET_OFF))
        sizes.append(1)
    _back(builder)
    sizes.append(1)
    builder.adjust(*sizes)
    return builder.as_markup()


def p2p_cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✖️ Отмена", callback_data=_p2p(P2PAction.HOME))
    return builder.as_markup()


def p2p_alert_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💱 Открыть курс", callback_data=_p2p(P2PAction.HOME))
    return builder.as_markup()


# ------------------------------------------------------------------------ 🔎 сайты
def _sites(action: SitesAction, ref: str = "", arg: str = "", ts: int = 0, go: int = 0) -> SitesCb:
    return SitesCb(action=action, ref=ref, arg=arg, ts=ts, go=go)


def sites_kb(sites: Sequence[Site]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, site in enumerate(sites[:SITES_SHOWN], 1):
        icon = "⏸" if site.paused else "⚠️" if site.error else "💰" if site.is_price else "📄"
        builder.button(text=truncate(f"{index}. {icon} {site.name}", 40), callback_data=_sites(SitesAction.CARD, site.uuid))
    builder.button(text="➕ Добавить", callback_data=_sites(SitesAction.ADD))
    builder.button(text="🔄 Проверить все", callback_data=_sites(SitesAction.CHECK_ALL))
    _back(builder)
    builder.adjust(*([1] * len(sites[:SITES_SHOWN])), 2, 1)
    return builder.as_markup()


def sites_cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✖️ Отмена", callback_data=_sites(SitesAction.HOME))
    return builder.as_markup()


def sites_mode_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Цена товара", callback_data=_sites(SitesAction.MODE, arg="p"))
    builder.button(text="📄 Любые изменения", callback_data=_sites(SitesAction.MODE, arg="g"))
    builder.button(text="✖️ Отмена", callback_data=_sites(SitesAction.HOME))
    builder.adjust(1, 1, 1)
    return builder.as_markup()


def site_kb(site: Site, *, has_target: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    sizes: list[int] = []
    if site.url.startswith(("http://", "https://")):
        builder.button(text="🔗 Открыть страницу", url=site.url)
        sizes.append(1)
    builder.button(text="🔄 Проверить сейчас", callback_data=_sites(SitesAction.CHECK, site.uuid))
    if site.paused:
        builder.button(text="▶️ Продолжить", callback_data=_sites(SitesAction.PAUSE, site.uuid, "u"))
    else:
        builder.button(text="⏸ Пауза", callback_data=_sites(SitesAction.PAUSE, site.uuid, "p"))
    sizes.append(2)
    builder.button(text="⏱ Частота", callback_data=_sites(SitesAction.EVERY, site.uuid))
    builder.button(
        text=f"🌐 Браузер: {'вкл' if site.browser else 'выкл'}",
        callback_data=_sites(SitesAction.BROWSER, site.uuid, "0" if site.browser else "1"),
    )
    sizes.append(2)
    if site.is_price:
        builder.button(text="📈 График цены", callback_data=_sites(SitesAction.CHART, site.uuid))
        builder.button(text="🎯 Цель" + (" ✓" if has_target else ""), callback_data=_sites(SitesAction.TARGET, site.uuid))
        sizes.append(2)
        if has_target:
            builder.button(text="❌ Убрать цель", callback_data=_sites(SitesAction.TARGET_OFF, site.uuid))
            sizes.append(1)
    builder.button(text="🗑 Удалить", callback_data=_sites(SitesAction.DELETE, site.uuid))
    builder.button(text="⬅️ К списку", callback_data=_sites(SitesAction.HOME))
    sizes.append(2)
    builder.adjust(*sizes)
    return builder.as_markup()


def site_confirm_kb(uuid: str, ts: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=_sites(SitesAction.DELETE, uuid, ts=ts, go=1))
    builder.button(text="❌ Отмена", callback_data=_sites(SitesAction.CARD, uuid))
    builder.adjust(2)
    return builder.as_markup()


def sites_alert_kb(site: Site) -> InlineKeyboardMarkup:
    """Под уведомлением об изменении: открыть страницу, карточка слежки."""
    builder = InlineKeyboardBuilder()
    if site.url.startswith(("http://", "https://")):
        builder.button(text="🔗 Открыть", url=site.url)
    builder.button(text="🔎 Слежка", callback_data=_sites(SitesAction.CARD, site.uuid))
    builder.adjust(2)
    return builder.as_markup()


# ------------------------------------------------------------------ ⬆️ обновление
def update_kb(*, previous: str | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if previous:
        builder.button(text=f"↩️ Откатить на v{previous}", callback_data=UpdateCb(action=UpdateAction.ROLLBACK))
    builder.button(text="⬅️ Настройки", callback_data=SetCb(sec=SetSection.ROOT))
    builder.adjust(1)
    return builder.as_markup()


def update_confirm_kb(sig: str, ts: int, *, can_install: bool, label: str = "✅ Установить") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_install:
        builder.button(text=label, callback_data=UpdateCb(action=UpdateAction.GO, sig=sig, ts=ts))
    builder.button(text="✖️ Отмена", callback_data=UpdateCb(action=UpdateAction.NO))
    builder.adjust(2 if can_install else 1)
    return builder.as_markup()


def rollback_confirm_kb(ts: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, откатить", callback_data=UpdateCb(action=UpdateAction.ROLLBACK, ts=ts, go=1))
    builder.button(text="❌ Отмена", callback_data=UpdateCb(action=UpdateAction.INFO))
    builder.adjust(2)
    return builder.as_markup()


def backup_kb(items: Sequence[BackupFile], tz: tzinfo | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💾 Сохранить сейчас", callback_data=BackupCb(action=BackupAction.NOW))
    if items:
        builder.button(text="📤 Прислать последнюю", callback_data=BackupCb(action=BackupAction.SEND))
    for item in items[:5]:
        builder.button(
            text=f"↩️ Восстановить {backup_when(item.ts, tz)}", callback_data=BackupCb(action=BackupAction.RESTORE, ts=item.ts)
        )
    builder.button(text="⬅️ Настройки", callback_data=SetCb(sec=SetSection.ROOT))
    builder.adjust(2, *([1] * (len(items[:5]) + 1)))
    return builder.as_markup()


def backup_confirm_kb(ts: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, восстановить", callback_data=BackupCb(action=BackupAction.RESTORE, ts=ts, go=1))
    builder.button(text="❌ Отмена", callback_data=BackupCb(action=BackupAction.HOME))
    builder.adjust(1)
    return builder.as_markup()


def voice_kb(ref: int, *, remind: bool, spend: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Заметка", callback_data=VoiceCb(action=VoiceAction.NOTE, ref=ref))
    if remind:
        builder.button(text="⏰ Напомнить", callback_data=VoiceCb(action=VoiceAction.REMIND, ref=ref))
    if spend:
        builder.button(text="💰 Расход", callback_data=VoiceCb(action=VoiceAction.SPEND, ref=ref))
    builder.button(text="❌ Ничего", callback_data=VoiceCb(action=VoiceAction.DROP, ref=ref))
    builder.adjust(2)
    return builder.as_markup()


def webapp_kb(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🖥 Открыть панель", web_app=WebAppInfo(url=url))]])


# ------------------------------------------------------------------- 👥 доступ
_GUEST_BUTTONS = (
    ("notes", "📝 Заметки", MenuAction.NOTES),
    ("reminders", "⏰ Напоминания", MenuAction.REMINDERS),
    ("p2p", "💱 P2P курс", MenuAction.P2P),
    ("news", "🗞 Новости", MenuAction.NEWS),
    ("tools", "🧰 Инструменты", MenuAction.TOOLS),
)


def guest_menu(perms: Sequence[str], web_url: str = "") -> InlineKeyboardMarkup:
    """Меню гостя: только то, что разрешил администратор (и «Панель», если она настроена)."""
    builder = InlineKeyboardBuilder()
    shown = 0
    for key, text, action in _GUEST_BUTTONS:
        if key in perms and action not in _HIDDEN:
            builder.button(text=text, callback_data=_menu(action))
            shown += 1
    if shown and web_url.startswith("https://"):
        builder.button(text="🖥 Панель", web_app=WebAppInfo(url=web_url))
    builder.adjust(2)
    return builder.as_markup() if shown else InlineKeyboardMarkup(inline_keyboard=[])


def access_list_kb(people: Sequence[Person], *, limit: int = 30) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for person in people[:limit]:
        builder.button(
            text=f"{STATUS_ICON[person.status]} {truncate(person.name, 30)}",
            callback_data=AccessCb(action=AccessAction.VIEW, uid=person.id),
        )
    builder.button(text="🔄 Обновить", callback_data=AccessCb(action=AccessAction.LIST))
    _back(builder, MenuAction.SETTINGS, "⬅️ Настройки")
    builder.adjust(*([1] * min(len(people), limit)), 2)
    return builder.as_markup()


def person_kb(person: Person) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, title in FEATURES.items():
        mark = "✅" if key in person.perms else "⬜"
        builder.button(text=f"{mark} {title}", callback_data=AccessCb(action=AccessAction.TOGGLE, uid=person.id, feat=key))
    if person.status != "approved":
        builder.button(text="✅ Принять", callback_data=AccessCb(action=AccessAction.APPROVE, uid=person.id))
    if person.status != "blocked":
        builder.button(text="🚫 Заблокировать", callback_data=AccessCb(action=AccessAction.BLOCK, uid=person.id))
    builder.button(text="🗑 Удалить", callback_data=AccessCb(action=AccessAction.REMOVE, uid=person.id))
    builder.button(text="⬅️ Все люди", callback_data=AccessCb(action=AccessAction.LIST))
    builder.adjust(*([1] * len(FEATURES)), 2, 2)
    return builder.as_markup()


def access_request_kb(uid: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принять", callback_data=AccessCb(action=AccessAction.APPROVE, uid=uid))
    builder.button(text="🚫 Заблокировать", callback_data=AccessCb(action=AccessAction.BLOCK, uid=uid))
    builder.button(text="⚙️ Права", callback_data=AccessCb(action=AccessAction.VIEW, uid=uid))
    builder.adjust(2, 1)
    return builder.as_markup()


def access_remove_kb(uid: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Да, удалить", callback_data=AccessCb(action=AccessAction.REMOVE_YES, uid=uid))
    builder.button(text="↩️ Нет", callback_data=AccessCb(action=AccessAction.VIEW, uid=uid))
    builder.adjust(2)
    return builder.as_markup()


# ------------------------------------------------------------------- 🧰 инструменты
def tools_menu_kb(*, merge: bool, download: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if merge:
        builder.button(text="📑 Собрать PDF", callback_data=ToolCb(act=ToolAction.MERGE))
    if download:
        builder.button(text="⬇️ По ссылке", callback_data=ToolCb(act=ToolAction.LINK))
    _back(builder)
    builder.adjust(2, 1)
    return builder.as_markup()


def tools_ops_kb(ref: int, ops: Sequence[Any]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, op in enumerate(ops):
        builder.button(text=op.label, callback_data=ToolCb(act=ToolAction.RUN, ref=ref, idx=i))
    builder.button(text="⬅️ Меню", callback_data=_menu(MenuAction.HOME))
    builder.adjust(*([2] * ((len(ops) + 1) // 2)), 1)
    return builder.as_markup()


def tools_collect_kb(count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if count:
        builder.button(text=f"✅ Собрать ({count})", callback_data=ToolCb(act=ToolAction.DONE))
    builder.button(text="✖️ Отмена", callback_data=ToolCb(act=ToolAction.CANCEL))
    builder.adjust(1, 1)
    return builder.as_markup()


def tools_link_kb(*, ffmpeg: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if ffmpeg:
        builder.button(text="🎵 Звук MP3", callback_data=ToolCb(act=ToolAction.GET, idx=0))
    builder.button(text="🎬 Видео", callback_data=ToolCb(act=ToolAction.GET, idx=1))
    builder.button(text="✖️ Отмена", callback_data=ToolCb(act=ToolAction.CANCEL))
    builder.adjust(2, 1)
    return builder.as_markup()


# ------------------------------------------------------------------- 🧩 приложения
def tiles_kb(rows: Sequence[tuple[str, str]], *, catalog: bool = False) -> InlineKeyboardMarkup:
    """rows: (service, подпись кнопки) — только то, что можно подключить или отключить из чата."""
    builder = InlineKeyboardBuilder()
    for service, text in rows:
        builder.button(text=truncate(text, 40), callback_data=TileCb(action=TileAction.OPEN, svc=service))
    if catalog:
        builder.button(text="📦 Каталог: установить новое", callback_data=TileCb(action=TileAction.CATALOG))
    builder.button(text="🧱 Модули: включить и выключить", callback_data=TileCb(action=TileAction.MODULES))
    builder.button(text="🔄 Искать снова", callback_data=TileCb(action=TileAction.LIST))
    builder.button(text="⬅️ Настройки", callback_data=SetCb(sec=SetSection.ROOT))
    builder.adjust(1)
    return builder.as_markup()


def tile_kb(service: str, *, connected: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if connected:
        builder.button(text="🔌 Отключить", callback_data=TileCb(action=TileAction.FORGET, svc=service))
    else:
        builder.button(text="➕ Подключить", callback_data=TileCb(action=TileAction.CONNECT, svc=service))
    builder.button(text="⬅️ Приложения", callback_data=TileCb(action=TileAction.LIST))
    builder.adjust(1)
    return builder.as_markup()


def tile_cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✖️ Отмена", callback_data=TileCb(action=TileAction.LIST))
    return builder.as_markup()


def modules_kb(rows: Sequence[tuple[str, str]]) -> InlineKeyboardMarkup:
    """rows: (id плитки, подпись); нажатие переключает."""
    builder = InlineKeyboardBuilder()
    for tile_id, text in rows:
        builder.button(text=truncate(text, 40), callback_data=TileCb(action=TileAction.TOGGLE, svc=tile_id))
    builder.button(text="⬅️ Приложения", callback_data=TileCb(action=TileAction.LIST))
    builder.adjust(1)
    return builder.as_markup()


def catalog_kb(items: Sequence[tuple[str, str]]) -> InlineKeyboardMarkup:
    """items: (имя в каталоге, подпись)."""
    builder = InlineKeyboardBuilder()
    for name, text in items:
        builder.button(text=truncate(text, 40), callback_data=TileCb(action=TileAction.INSTALL, svc=name))
    builder.button(text="⬅️ Приложения", callback_data=TileCb(action=TileAction.LIST))
    builder.adjust(1)
    return builder.as_markup()


def repair_kb(actions: Sequence[tuple[str, str]]) -> InlineKeyboardMarkup:
    """actions: (id действия, подпись)."""
    builder = InlineKeyboardBuilder()
    for act, text in actions:
        builder.button(text=truncate(text, 40), callback_data=RepairCb(act=act))
    builder.button(text="⬅️ Самопроверка", callback_data=_menu(MenuAction.DOCTOR))
    builder.adjust(1)
    return builder.as_markup()
