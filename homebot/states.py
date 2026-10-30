"""FSM-состояния бота."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class OllamaStates(StatesGroup):
    """Режимы работы с локальной Ollama."""

    chat = State()  # диалог с моделью (с историей)
    summarize = State()  # ждём текст для краткого резюме


class ReminderStates(StatesGroup):
    add = State()  # ждём «когда + текст»


class SettingsStates(StatesGroup):
    add_feed = State()  # ждём ссылку на RSS-ленту


class NoteStates(StatesGroup):
    add = State()  # ждём текст заметки


class MovieStates(StatesGroup):
    search = State()  # ждём название фильма
    mood = State()  # ждём описание «что хочется посмотреть»


class P2PStates(StatesGroup):
    target = State()  # ждём целевую цену продажи


class FinanceStates(StatesGroup):
    expense = State()  # ждём «кофе 85»
    income = State()  # ждём «5000 зарплата»


class SitesStates(StatesGroup):
    url = State()  # ждём ссылку на страницу или товар
    target = State()  # ждём цену-цель для слежки


class ToolsStates(StatesGroup):
    merge = State()  # собираем PDF из присланных файлов
    link = State()  # ждём ссылку для скачивания


class TileStates(StatesGroup):
    field = State()  # ждём адрес / ключ / логин для подключаемого приложения


class RepairStates(StatesGroup):
    pin = State()  # ждём PIN для выбранного действия
