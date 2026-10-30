"""Локальный счётчик нажатий по разделам меню (только на этом сервере, никуда не отправляется)."""

from __future__ import annotations

from homebot.services.state import StateStore

STATE_KEY = "usage"
MAX_KEYS = 64

LABELS = {
    "news": "🗞 Новости", "srv": "🖥 Сервер", "set": "⚙️ Настройки", "status": "📊 Состояние", "live": "📈 Live",
    "charts": "📉 Графики", "svc": "🧩 Службы", "logs": "📜 Журналы", "top": "🔝 Процессы", "net": "🌐 Сеть",
    "sec": "🛡 Безопасность", "power": "⚡ Питание", "tasks": "🔔 Задачи", "ollama": "🤖 ИИ", "rem": "⏰ Напоминания",
    "notes": "📝 Заметки", "apps": "🐳 Приложения", "mov": "🎬 Кино", "fin": "💰 Финансы", "doc": "🩺 Самопроверка",
    "p2p": "💱 P2P", "sites": "🔎 Сайты", "tools": "🧰 Инструменты",
}  # fmt: skip


async def count(store: StateStore, section: str) -> None:
    data = dict(store.get(STATE_KEY) or {})
    if section not in data and len(data) >= MAX_KEYS:
        return
    data[section] = int(data.get(section, 0)) + 1
    await store.set(STATE_KEY, data)


def top(store: StateStore, limit: int = 12) -> list[tuple[str, int]]:
    data = store.get(STATE_KEY)
    if not isinstance(data, dict):
        return []
    items = [(str(k), int(v)) for k, v in data.items() if isinstance(v, int | float)]
    return sorted(items, key=lambda item: (-item[1], item[0]))[:limit]


def section_of(callback_data: str | None) -> str | None:
    """`m:sites` → `sites` (считаем только нажатия кнопок главного меню/разделов)."""
    if callback_data and callback_data.startswith("m:"):
        name = callback_data[2:]
        if name not in ("home", "live_stop"):
            return name
    return None


def render(items: list[tuple[str, int]]) -> str:
    if not items:
        return "📊 <b>Чем вы пользуетесь</b>\n\nПока пусто — считаю нажатия кнопок разделов."
    width = max(count_ for _, count_ in items)
    lines = ["📊 <b>Чем вы пользуетесь</b>", "<i>Счётчик только на сервере, никуда не уходит.</i>", ""]
    for name, value in items:
        bar = "▰" * max(1, round(8 * value / width))
        lines.append(f"{LABELS.get(name, name)} — {value} {bar}")
    return "\n".join(lines)
