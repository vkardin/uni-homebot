"""Настройки, которые меняются прямо в боте (⚙️ Настройки) и переживают перезапуск.

Начальные значения берутся из .env; после первого изменения в боте действует сохранённое значение.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

from homebot.config import Settings
from homebot.services.state import StateStore

_KEY = "prefs.news"
_LIST_FIELDS = ("feeds",)  # хранятся в JSON списками, в памяти — кортежами

INTERVAL_CHOICES = (30, 60, 120, 180, 360)
COUNT_CHOICES = (3, 5, 7, 10)
QUIET_CHOICES: tuple[tuple[int, int], ...] = ((23, 8), (22, 8), (0, 8), (23, 9), (22, 7))
MODE_DAY = "day"  # одно сообщение на день, обновляется каждый час
MODE_NEW = "new"  # новое сообщение каждый раз


def hour_in_range(hour: int, start: int, end: int) -> bool:
    """Час внутри [start, end) с учётом перехода через полночь; start == end — диапазон пуст."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _parse_quiet(value: str) -> tuple[int, int] | None:
    if not value:
        return None
    start, end = value.split("-")
    return int(start), int(end)


@dataclass(frozen=True, slots=True)
class NewsPrefs:
    enabled: bool = True
    interval_min: int = 60
    count: int = 5
    quiet: bool = True
    quiet_from: int = 23
    quiet_to: int = 8
    retell: bool = True
    fetch_articles: bool = True  # если у новости нет описания — читать начало статьи
    feeds: tuple[str, ...] = field(default_factory=tuple)
    mode: str = MODE_DAY
    model: str = ""  # модель Ollama для пересказа; пусто — та же, что в чате

    def is_quiet(self, hour: int) -> bool:
        return self.quiet and hour_in_range(hour, self.quiet_from, self.quiet_to)

    @property
    def mode_label(self) -> str:
        return "одно сообщение на день" if self.mode == MODE_DAY else "новое каждый раз"

    @property
    def interval_label(self) -> str:
        if self.interval_min < 60:
            return f"каждые {self.interval_min} мин"
        hours = self.interval_min // 60
        return "каждый час" if hours == 1 else f"каждые {hours} ч"


_NIGHT_KEY = "prefs.night"
_TRANSLATE_MODEL_KEY = "prefs.translate_model"
_P2P_KEY = "prefs.p2p"
P2P_THRESHOLDS = (0.5, 1.0, 2.0, 3.0, 0.0)  # % изменения для уведомления; 0 — выключено


@dataclass(frozen=True, slots=True)
class NightPrefs:
    """🌙 Ночной режим для ВСЕХ уведомлений: важное — со звуком, остальное — беззвучно, необязательное ждёт утра."""

    enabled: bool = True
    start: int = 23
    end: int = 8

    def is_quiet(self, hour: int) -> bool:
        return self.enabled and hour_in_range(hour, self.start, self.end)

    @property
    def label(self) -> str:
        return f"{self.start:02d}:00–{self.end:02d}:00" if self.enabled else "выключен"


@dataclass(frozen=True, slots=True)
class P2PPrefs:
    """💱 Уведомления о P2P-курсе: резкое изменение, целевая цена, вечерняя сводка."""

    threshold: float = 1.0  # % от последней сообщённой цены; 0 — не сообщать об изменениях
    target: float = 0.0  # цена продажи, при достижении которой сообщить; 0 — цели нет
    summary: bool = True


def next_choice(choices: tuple[Any, ...], current: Any) -> Any:
    """Следующее значение по кругу (кнопка «переключить»)."""
    try:
        return choices[(choices.index(current) + 1) % len(choices)]
    except ValueError:
        return choices[0]


class Prefs:
    def __init__(self, store: StateStore, settings: Settings) -> None:
        self._store = store
        self._translate_default = str(getattr(settings, "translate_model", "") or "")
        quiet = _parse_quiet(settings.news_quiet_hours)
        self._defaults = NewsPrefs(
            enabled=settings.news_enabled,
            interval_min=settings.news_interval_min,
            count=settings.news_count,
            quiet=quiet is not None,
            quiet_from=quiet[0] if quiet else 23,
            quiet_to=quiet[1] if quiet else 8,
            feeds=tuple(settings.feeds),
        )

    def translate_model(self) -> str:
        """🌐 Модель Ollama для перевода: выбранная в боте, иначе TRANSLATE_MODEL из .env; пусто — как в чате."""
        saved = self._store.get(_TRANSLATE_MODEL_KEY)
        return saved if isinstance(saved, str) else self._translate_default

    async def set_translate_model(self, name: str) -> None:
        await self._store.set(_TRANSLATE_MODEL_KEY, name)

    def p2p(self) -> P2PPrefs:
        saved = self._store.get(_P2P_KEY)
        if not isinstance(saved, dict):
            return P2PPrefs()
        try:
            threshold = float(saved.get("threshold", 1.0))
            target = float(saved.get("target", 0.0))
        except (TypeError, ValueError):
            return P2PPrefs()
        return P2PPrefs(max(threshold, 0.0), max(target, 0.0), bool(saved.get("summary", True)))

    async def update_p2p(self, **changes: Any) -> P2PPrefs:
        updated = replace(self.p2p(), **changes)
        await self._store.set(_P2P_KEY, asdict(updated))
        return updated

    def night(self) -> NightPrefs:
        saved = self._store.get(_NIGHT_KEY)
        news = self.news()
        default = NightPrefs(enabled=news.quiet, start=news.quiet_from, end=news.quiet_to)  # как тихие часы новостей
        if not isinstance(saved, dict):
            return default
        try:
            return NightPrefs(bool(saved.get("enabled", default.enabled)), int(saved["start"]) % 24, int(saved["end"]) % 24)
        except (KeyError, TypeError, ValueError):
            return default

    async def update_night(self, **changes: Any) -> NightPrefs:
        updated = replace(self.night(), **changes)
        await self._store.set(_NIGHT_KEY, asdict(updated))
        return updated

    def news(self) -> NewsPrefs:
        saved = self._store.get(_KEY)
        if not isinstance(saved, dict):
            return self._defaults
        known = {f.name for f in fields(NewsPrefs)}
        values = {k: v for k, v in saved.items() if k in known}
        for name in _LIST_FIELDS:
            if name in values:
                raw = values[name]
                values[name] = tuple(str(u) for u in raw if isinstance(u, str)) if isinstance(raw, list | tuple) else ()
        try:
            return replace(self._defaults, **values)
        except TypeError:
            return self._defaults

    async def update_news(self, **changes: Any) -> NewsPrefs:
        updated = replace(self.news(), **changes)
        data = asdict(updated)
        for name in _LIST_FIELDS:
            data[name] = list(getattr(updated, name))
        await self._store.set(_KEY, data)
        return updated
