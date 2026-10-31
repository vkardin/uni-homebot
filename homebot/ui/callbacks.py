"""Фабрики callback-данных для inline-кнопок (лимит Telegram — 64 байта, держим их короткими)."""

from __future__ import annotations

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class MenuAction(StrEnum):
    HOME = "home"
    NEWS = "news"
    SERVER = "srv"
    SETTINGS = "set"
    STATUS = "status"
    LIVE = "live"
    LIVE_STOP = "live_stop"
    CHARTS = "charts"
    SERVICES = "svc"
    LOGS = "logs"
    TOP = "top"
    NETWORK = "net"
    SECURITY = "sec"
    POWER = "power"
    TASKS = "tasks"
    OLLAMA = "ollama"
    REMINDERS = "rem"
    NOTES = "notes"
    APPS = "apps"
    MOVIES = "mov"
    FINANCE = "fin"
    DOCTOR = "doc"
    REPAIR = "fix"
    P2P = "p2p"
    SITES = "sites"
    TOOLS = "tools"


class MenuCb(CallbackData, prefix="m"):
    action: MenuAction


class TaskCb(CallbackData, prefix="t"):
    """Переключение фоновой задачи по её имени."""

    name: str


class OllamaAction(StrEnum):
    PANEL = "panel"
    CHAT = "chat"
    SUMMARIZE = "sum"
    MODELS = "models"
    PICK = "pick"
    UNLOAD = "unload"


class OllamaCb(CallbackData, prefix="o"):
    action: OllamaAction
    idx: int = -1  # индекс модели в списке (для PICK)


class ReportCb(CallbackData, prefix="rp"):
    period: str  # week | month | year (homebot.services.reports.PERIODS)


class ChartCb(CallbackData, prefix="c"):
    period: str  # ключ из homebot.services.history.PERIODS


class LogAction(StrEnum):
    SHOW = "show"
    EXPLAIN = "ai"


class LogCb(CallbackData, prefix="g"):
    action: LogAction
    idx: int  # индекс службы в списке LogsService.units


class PowerAction(StrEnum):
    RESTART = "rs"
    REBOOT = "rb"
    POWEROFF = "off"


class PowerCb(CallbackData, prefix="p"):
    action: PowerAction
    idx: int = -1  # индекс службы (для RESTART)
    ts: int = 0  # когда показано подтверждение (unix-время)
    go: int = 0  # 0 — спросить подтверждение, 1 — выполнить


class ItemAction(StrEnum):
    ADD = "add"
    DELETE = "del"


class ReminderCb(CallbackData, prefix="r"):
    action: ItemAction
    id: int = 0


class NoteCb(CallbackData, prefix="n"):
    action: ItemAction
    id: int = 0


class App(StrEnum):
    DOCKER = "dk"
    SPEEDTEST = "st"
    QBIT = "qb"
    ADGUARD = "ag"
    JELLYFIN = "jf"


class AppAction(StrEnum):
    VIEW = "v"  # экран приложения
    CONTAINER = "c"  # карточка контейнера (arg = имя)
    RESTART = "rs"  # перезапуск контейнера (arg = имя, ts/go — подтверждение)
    EXPLAIN = "ai"  # разбор журнала контейнера моделью
    RUN = "run"  # запустить замер скорости
    CHART = "ch"  # график скорости
    PAUSE = "pa"  # торренты: всё на паузу
    RESUME = "re"  # торренты: продолжить всё
    OFF = "off"  # AdGuard: выключить защиту на 10 минут
    ON = "on"  # AdGuard: включить защиту


class AppCb(CallbackData, prefix="a"):
    app: App
    action: AppAction
    arg: str = ""
    ts: int = 0
    go: int = 0


class NewsAction(StrEnum):
    NOW = "now"  # собрать свежие прямо сейчас


class NewsCb(CallbackData, prefix="w"):
    action: NewsAction


class SetSection(StrEnum):
    ROOT = "root"
    NEWS = "news"
    FEEDS = "feeds"
    PRESETS = "pre"
    MODEL = "mdl"
    NIGHT = "nt"  # 🌙 ночной режим для всех уведомлений
    TRANSLATE = "tr"  # 🌐 модель для перевода


class SetAction(StrEnum):
    VIEW = "v"
    ENABLED = "en"  # вкл/выкл рассылку
    INTERVAL = "int"  # следующая частота
    COUNT = "cnt"  # следующее количество
    QUIET = "q"  # вкл/выкл тихие часы
    QUIET_RANGE = "qr"  # следующий диапазон тихих часов
    RETELL = "rt"  # вкл/выкл пересказ
    ARTICLES = "art"  # читать статью, если нет описания
    DELETE = "del"  # удалить ленту idx
    ADD = "add"  # добавить ленту по ссылке
    PRESET = "p"  # добавить подборку idx
    MODE = "md"  # одно сообщение на день / новое каждый раз
    MODEL = "ml"  # выбор модели для новостей (idx -1 — как в чате)


class SetCb(CallbackData, prefix="s"):
    sec: SetSection
    act: SetAction = SetAction.VIEW
    idx: int = -1


class MovieAction(StrEnum):
    HOME = "h"  # экран «🎬 Кино»
    SEARCH = "s"  # ждём название
    MOODS = "ms"  # выбор настроения
    MOOD = "m"  # подбор по настроению (arg = ключ)
    CUSTOM = "mc"  # подбор «своими словами»
    TRENDING = "tr"  # популярное сейчас
    LIBRARY = "lib"  # что посмотреть из скачанного
    CARD = "c"  # карточка фильма (ref = tmdb)
    ADD = "add"  # скачать фильм
    RESEARCH = "rs"  # искать раздачу ещё раз
    TV = "tv"  # смотреть на ТВ (arg r — продолжить)
    S_CARD = "sc"  # карточка сериала (ref = tvdb)
    S_ADD = "sa"  # скачать сериал (arg all — все сезоны, last — последний)
    S_RESEARCH = "sr"  # искать серии ещё раз
    S_TV = "st"  # следующая серия на ТВ


class MovieCb(CallbackData, prefix="f"):
    action: MovieAction
    ref: int = 0  # tmdb фильма или tvdb сериала
    arg: str = ""


class CleanAction(StrEnum):
    VIEW = "v"  # экран «Чистка диска»
    DEL = "d"  # удалить один (kind, ref); go=0 — спросить, go=1 — выполнить
    ALL = "all"  # удалить всё показанное (ref — сколько ожидалось)


class CleanCb(CallbackData, prefix="x"):
    action: CleanAction
    kind: str = ""  # m — фильм (Radarr), s — сериал (Sonarr)
    ref: int = 0
    ts: int = 0
    go: int = 0


class ExpenseAction(StrEnum):
    """💰 Финансы (Actual Budget). draft — номер черновика/записи/снимка списка, idx — позиция в списке."""

    HOME = "h"  # экран «Финансы»
    NEW_EXPENSE = "nx"  # ждём «кофе 85»
    NEW_INCOME = "ni"  # ждём «5000 зарплата»
    SAVE = "ok"  # записать черновик в Actual
    CANCEL = "no"
    CATS = "cs"  # список категорий для черновика
    PICK = "pc"  # выбрать категорию idx (-1 — назад к карточке)
    DRAFT_ACCOUNTS = "da"  # список счетов для черновика
    PICK_ACCOUNT = "pa"  # выбрать счёт idx для черновика (-1 — назад)
    UNDO = "un"  # отменить только что записанную операцию
    OPS = "op"  # последние операции
    DELETE = "dl"  # удалить операцию idx из снимка draft (подтверждение)
    DELETE_OK = "dk"
    BUDGET = "bg"  # бюджет месяца; idx — сдвиг от текущего месяца (0, -1, ...)
    ACCOUNTS = "as"  # выбрать счёт для записи по умолчанию
    SET_ACCOUNT = "sa"


class ExpenseCb(CallbackData, prefix="e"):
    action: ExpenseAction
    draft: int = 0  # номер черновика
    idx: int = -1  # индекс категории в списке


class P2PAction(StrEnum):
    """💱 P2P-курс Binance."""

    HOME = "h"  # экран с текущим курсом
    CHART = "ch"  # график за 7 дней
    THRESHOLD = "th"  # следующий порог резкого изменения
    TARGET = "tg"  # ждём целевую цену
    TARGET_OFF = "to"  # убрать цель
    SUMMARY = "sm"  # вечерняя сводка вкл/выкл


class P2PCb(CallbackData, prefix="y"):
    action: P2PAction


class SitesAction(StrEnum):
    """🔎 Сайты (changedetection.io). ref — uuid слежки."""

    HOME = "h"  # список слежек
    ADD = "add"  # ждём ссылку
    MODE = "md"  # режим новой слежки (arg: p — цена, g — любые изменения)
    CARD = "c"  # карточка слежки
    CHECK = "ck"  # проверить сейчас
    CHECK_ALL = "ca"  # проверить все
    PAUSE = "pa"  # arg: p — на паузу, u — продолжить
    EVERY = "ev"  # следующая частота проверки
    DELETE = "dl"  # удалить; go=0 — спросить, go=1 — выполнить
    CHART = "gr"  # график цены
    TARGET = "tg"  # ждём цену-цель
    TARGET_OFF = "to"  # убрать цель
    BROWSER = "br"  # открывать страницу браузером: arg 1 — вкл, 0 — выкл


class SitesCb(CallbackData, prefix="z"):
    action: SitesAction
    ref: str = ""
    arg: str = ""
    ts: int = 0
    go: int = 0


class UpdateAction(StrEnum):
    """⬆️ Обновление из чата."""

    INFO = "i"  # экран «Обновление»
    GO = "go"  # установить присланный архив (sig — начало его sha256)
    NO = "no"  # отмена
    ROLLBACK = "rb"  # вернуть прежнюю версию; go=0 — спросить, go=1 — выполнить
    FETCH = "f"  # 🆕 скачать релиз с GitHub (sig — версия)


class UpdateCb(CallbackData, prefix="u"):
    action: UpdateAction
    sig: str = ""
    ts: int = 0
    go: int = 0


class BackupAction(StrEnum):
    """💾 Бэкап."""

    HOME = "h"
    NOW = "now"  # сохранить сейчас
    SEND = "snd"  # прислать файл в чат (ts — какой; 0 = последний)
    RESTORE = "rs"  # восстановить; go=0 — спросить, go=1 — выполнить
    IMPORT = "imp"  # 📦 принять присланный uni-settings.json; go=1 — применить


class BackupCb(CallbackData, prefix="b"):
    action: BackupAction
    ts: int = 0
    go: int = 0


class VoiceAction(StrEnum):
    """🎙 Что сделать с распознанным голосовым."""

    NOTE = "n"
    REMIND = "r"
    SPEND = "s"
    DROP = "x"


class VoiceCb(CallbackData, prefix="v"):
    action: VoiceAction
    ref: int


class AccessAction(StrEnum):
    LIST = "ls"
    VIEW = "v"
    TOGGLE = "t"
    APPROVE = "ok"
    BLOCK = "bl"
    REMOVE = "rm"
    REMOVE_YES = "rmy"


class AccessCb(CallbackData, prefix="k"):
    """👥 Доступ: действие над человеком (uid — Telegram ID) и, для переключателя, ключ функции."""

    action: AccessAction
    uid: int = 0
    feat: str = ""


class ToolAction(StrEnum):
    RUN = "r"  # применить операцию idx к файлу ref
    MERGE = "m"  # режим «собрать PDF из фото и файлов»
    DONE = "d"  # собрать
    LINK = "l"  # режим «скачать по ссылке»
    GET = "g"  # скачать: idx 0 — звук MP3, 1 — видео
    CANCEL = "c"


class ToolCb(CallbackData, prefix="j"):
    """🧰 Инструменты: ref — номер присланного файла, idx — номер операции в списке для этого файла."""

    act: ToolAction
    ref: int = 0
    idx: int = 0


class TileAction(StrEnum):
    LIST = "ls"
    OPEN = "op"
    CONNECT = "cn"
    FORGET = "fg"
    CATALOG = "ct"
    INSTALL = "in"
    MODULES = "md"
    TOGGLE = "tg"


class TileCb(CallbackData, prefix="l"):
    """🧩 Подключение приложений из чата."""

    action: TileAction
    svc: str = ""


class RepairCb(CallbackData, prefix="d"):
    """🛠 Ремонт сервера: действие из белого списка (act — id действия, «» — список)."""

    act: str = ""
