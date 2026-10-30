"""👥 Доступ к боту: администратор принимает заявки и выбирает, чем может пользоваться каждый человек.

Данные лежат в state.json (ключ `access`). Всё, что не из списка функций гостя, ему недоступно по умолчанию.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

STATE_KEY = "access"
MAX_PEOPLE = 60  # потолок записей: защита от заваливания заявками
MAX_PENDING = 20

PENDING, APPROVED, BLOCKED = "pending", "approved", "blocked"
STATUS_ICON = {PENDING: "🕓", APPROVED: "✅", BLOCKED: "🚫"}

# Что администратор может разрешить человеку (ключ → название). Добавляются по мере появления «личных» версий разделов.
FEATURES: dict[str, str] = {
    "notes": "📝 Заметки",
    "reminders": "⏰ Напоминания",
    "p2p": "💱 Курс P2P",
    "news": "🗞 Новости",
    "tools": "🧰 Конвертер файлов",
    "download": "⬇️ Скачивание по ссылке",
}
DEFAULT_PERMS = ("notes", "reminders", "p2p", "news")
LIMITS = {"notes": 100, "reminders": 30}  # на человека
MAX_GUEST_FEEDS = 8  # своих источников новостей у одного человека (кроме готовых подборок)
GUEST_COUNTS = (3, 5, 8, 10)  # сколько новостей показывать


@dataclass(frozen=True, slots=True)
class Person:
    id: int
    name: str
    username: str
    status: str
    perms: tuple[str, ...]
    created: int
    seen: int = 0
    prefs: dict[str, Any] = field(default_factory=dict, compare=False)  # личные настройки: см. clean_prefs

    def can(self, feature: str) -> bool:
        return self.status == APPROVED and feature in self.perms

    @property
    def label(self) -> str:
        return self.name + (f" (@{self.username})" if self.username else "")


def _clean(text: object, limit: int) -> str:
    return "".join(ch for ch in str(text or "") if ch.isprintable())[:limit]


def clean_prefs(raw: Any) -> dict[str, Any]:
    """Личные настройки человека: только известные поля и безопасные значения."""
    from homebot.services.presets import PRESET_KEYS

    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    nick = _clean(raw.get("nick"), 32).strip()
    if nick:
        out["nick"] = nick
    if raw.get("count") in GUEST_COUNTS:
        out["count"] = raw["count"]
    presets = raw.get("presets")
    if isinstance(presets, list | tuple):
        out["presets"] = [k for k in PRESET_KEYS if k in presets]
    feeds = raw.get("feeds")
    if isinstance(feeds, list | tuple):
        seen: list[str] = []
        for u in feeds:
            if isinstance(u, str) and u.startswith(("http://", "https://")) and len(u) <= 300 and u not in seen:
                seen.append(u)
        out["feeds"] = seen[:MAX_GUEST_FEEDS]
    return out


def _parse(uid: str, raw: Any) -> Person | None:
    if not isinstance(raw, dict):
        return None
    try:
        perms = tuple(p for p in raw.get("perms", ()) if p in FEATURES)
        status = raw.get("status") if raw.get("status") in STATUS_ICON else PENDING
        return Person(
            int(uid),
            _clean(raw.get("name"), 64) or uid,
            _clean(raw.get("username"), 40),
            status,
            perms,
            int(raw.get("created", 0)),
            int(raw.get("seen", 0)),
            clean_prefs(raw.get("prefs")),
        )
    except (TypeError, ValueError):
        return None


class AccessService:
    def __init__(self, store: Any, admin_id: int) -> None:
        self._store = store
        self._admin = admin_id
        self._notified: dict[int, float] = {}

    # ------------------------------------------------------------------ чтение
    def _raw(self) -> dict[str, Any]:
        data = self._store.get(STATE_KEY)
        return dict(data) if isinstance(data, dict) else {}

    def people(self) -> list[Person]:
        found = [p for uid, raw in self._raw().items() if (p := _parse(uid, raw)) is not None]
        order = {PENDING: 0, APPROVED: 1, BLOCKED: 2}
        return sorted(found, key=lambda p: (order[p.status], -p.created))

    def get(self, user_id: int) -> Person | None:
        return _parse(str(user_id), self._raw().get(str(user_id)))

    # ------------------------------------------------------------------ запись
    async def _save(self, data: dict[str, Any]) -> None:
        await self._store.set(STATE_KEY, data)

    @staticmethod
    def _dump(p: Person) -> dict[str, Any]:
        return {
            "name": p.name,
            "username": p.username,
            "status": p.status,
            "perms": list(p.perms),
            "created": p.created,
            "seen": p.seen,
            "prefs": p.prefs,
        }

    async def register(self, user_id: int, name: str, username: str | None) -> tuple[Person | None, bool]:
        """Заявка нового человека. (запись, создана ли сейчас). None — места нет (слишком много заявок)."""
        data = self._raw()
        existing = _parse(str(user_id), data.get(str(user_id)))
        if existing is not None:
            return existing, False
        pending = sum(1 for raw in data.values() if isinstance(raw, dict) and raw.get("status") == PENDING)
        if len(data) >= MAX_PEOPLE or pending >= MAX_PENDING:
            return None, False
        person = Person(user_id, _clean(name, 64) or str(user_id), _clean(username, 40), PENDING, DEFAULT_PERMS, int(time.time()))
        data[str(user_id)] = self._dump(person)
        await self._save(data)
        return person, True

    async def _update(self, user_id: int, **changes: Any) -> Person | None:
        data = self._raw()
        person = _parse(str(user_id), data.get(str(user_id)))
        if person is None:
            return None
        fields = {**self._dump(person), **changes}
        data[str(user_id)] = fields
        await self._save(data)
        return _parse(str(user_id), fields)

    async def set_status(self, user_id: int, status: str) -> Person | None:
        return await self._update(user_id, status=status) if status in STATUS_ICON else None

    async def toggle(self, user_id: int, feature: str) -> Person | None:
        person = self.get(user_id)
        if person is None or feature not in FEATURES:
            return None
        perms = [p for p in person.perms if p != feature] if feature in person.perms else [*person.perms, feature]
        return await self._update(user_id, perms=perms)

    async def set_prefs(self, user_id: int, **changes: Any) -> Person | None:
        """Меняет личные настройки; None в значении — сбросить поле."""
        person = self.get(user_id)
        if person is None:
            return None
        merged = {**person.prefs, **changes}
        return await self._update(user_id, prefs=clean_prefs({k: v for k, v in merged.items() if v is not None}))

    async def touch(self, user_id: int) -> None:
        person = self.get(user_id)
        if person is not None and time.time() - person.seen > 300:
            await self._update(user_id, seen=int(time.time()))

    async def remove(self, user_id: int) -> bool:
        data = self._raw()
        if data.pop(str(user_id), None) is None:
            return False
        await self._save(data)
        return True

    # --------------------------------------------------------------- вежливость
    def should_remind(self, user_id: int, *, every: float = 6 * 3600) -> bool:
        """Не чаще раза в `every` секунд отвечать ожидающему «заявка ещё на рассмотрении»."""
        now = time.monotonic()
        last = self._notified.get(user_id)
        if last is not None and now - last < every:
            return False
        if len(self._notified) > 500:
            self._notified.clear()
        self._notified[user_id] = now
        return True
