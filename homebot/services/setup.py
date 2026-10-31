"""🧭 «Начало работы»: что в UNI уже настроено, а что ещё нет.

Одни и те же шаги показывает /setup в чате и карточка на главной панели. Шаг считается сделанным по настоящему
состоянию (настройки, подключённые плитки, источники новостей), поэтому список ничего не хранит, кроме флага «скрыть».
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from homebot.i18n import tr
from homebot.services import tiles

if TYPE_CHECKING:
    from homebot.config import Settings
    from homebot.services.apps import Apps
    from homebot.services.state import StateStore

KEY_HIDDEN = "setup_hidden"


@dataclass(frozen=True, slots=True)
class Step:
    id: str
    title: str
    done: bool
    hint: str  # что сделать, одной строкой
    panel: str = ""  # раздел панели, где это делается; пусто — в чате или на сервере


def steps(settings: Settings, apps: Apps, news_feeds: list[str] | tuple[str, ...] = ()) -> list[Step]:
    connected = tiles.connected_ids(apps)
    tz_set = "timezone" in settings.model_fields_set or settings.timezone != "UTC"
    return [
        Step("owner", tr("Владелец назначен"), settings.admin_id is not None, tr("Напишите боту код из журнала сервера")),
        Step(
            "panel",
            tr("Панель открывается из Telegram"),
            bool(settings.web_enabled and settings.web_url),
            "На сервере: sudo bash deploy/update.sh — он включит Tailscale Serve и пропишет WEB_URL",
        ),
        Step("timezone", tr("Часовой пояс"), tz_set, f"Сейчас {settings.timezone}. Задаётся в Настройках", "more/settings"),
        Step(
            "weather", tr("Погода для вашего города"), settings.weather_enabled, tr("Укажите координаты в Настройках"), "more/settings"
        ),
        Step(
            "news",
            tr("Источники новостей"),
            bool(news_feeds or settings.feeds),
            tr("Выберите подборки или добавьте свои ленты"),
            "more/settings",
        ),
        Step("apps", tr("Подключено хотя бы одно приложение"), bool(connected), tr("В чате: /connect — найду контейнеры сам"), "more"),
    ]


def progress(items: list[Step]) -> tuple[int, int]:
    return sum(1 for s in items if s.done), len(items)


def hidden(store: StateStore) -> bool:
    return bool(store.get(KEY_HIDDEN, False))


async def set_hidden(store: StateStore, value: bool) -> None:
    await store.set(KEY_HIDDEN, bool(value))


def as_json(items: list[Step], store: StateStore) -> dict[str, Any]:
    done, total = progress(items)
    return {"steps": [asdict(s) for s in items], "done": done, "total": total, "hidden": hidden(store)}


def render(items: list[Step]) -> str:
    """Текст для /setup (HTML)."""
    done, total = progress(items)
    head = f"🧭 <b>{tr('Начало работы')}</b>" + (
        f" — {tr('всё готово')} ✨" if done == total else f": {tr('готово')} {done} {tr('из')} {total}"
    )
    lines = [head, ""]
    for s in items:
        lines.append(("✅ " if s.done else "▫️ ") + s.title + ("" if s.done else f"\n     <i>{s.hint}</i>"))
    if done < total:
        lines += ["", "Всё это есть и в панели (/app): карточка на главной."]
    return "\n".join(lines)
