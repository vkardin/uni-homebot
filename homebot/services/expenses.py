"""Черновики трат и память «слово → категория».

Черновик живёт в памяти бота недолго: пока вы не нажали «Записать» или «Отмена». Если бот перезапустится,
старая кнопка просто скажет, что карточка устарела — в Actual при этом ничего не попадёт.
Категории бот запоминает сам: выбрали «Кафе» для «кофе» один раз — дальше «кофе 85» подставит «Кафе».
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from homebot.services.actual import NOTE_MARK, Category, Txn
from homebot.services.expense_parse import ParsedExpense
from homebot.services.wordmatch import matches

KEY_WORDS = "expenses.words"  # {"кофе": "<id категории>"}
KEY_ACCOUNT = "finance.account"  # id счёта, на который записывать по умолчанию (выбирается в боте)
KEY_SAVED = "expenses.saved"
UNDO_TTL = 24 * 3600
MAX_DRAFTS = 20
DRAFT_TTL = 3600
MAX_WORDS_MEMORY = 300


@dataclass(frozen=True, slots=True)
class Draft:
    number: int
    parsed: ParsedExpense
    day: str  # ГГГГ-ММ-ДД
    category_id: str = ""
    created: float = 0.0
    account_id: str = ""

    def to_txn(self) -> Txn:
        return Txn(self.day, self.parsed.cents, self.category_id, self.parsed.note[:60], NOTE_MARK)


class DraftBox:
    """Номерные черновики: номер идёт в callback-данные кнопок."""

    def __init__(self, clock: Any = time.time) -> None:
        self._clock = clock
        self._items: dict[int, Draft] = {}
        self._next = int(clock()) % 100_000  # после рестарта старые кнопки не подхватят чужую карточку

    def new(self, parsed: ParsedExpense, day: str, category_id: str = "", account_id: str = "") -> Draft:
        self._expire()
        self._next += 1
        draft = Draft(self._next, parsed, day, category_id, self._clock(), account_id)
        self._items[draft.number] = draft
        while len(self._items) > MAX_DRAFTS:
            self._items.pop(next(iter(self._items)))
        return draft

    def get(self, number: int) -> Draft | None:
        self._expire()
        return self._items.get(number)

    def set_category(self, number: int, category_id: str) -> Draft | None:
        draft = self.get(number)
        if draft is None:
            return None
        updated = replace(draft, category_id=category_id)
        self._items[number] = updated
        return updated

    def set_account(self, number: int, account_id: str) -> Draft | None:
        draft = self.get(number)
        if draft is None:
            return None
        updated = replace(draft, account_id=account_id)
        self._items[number] = updated
        return updated

    def take(self, number: int) -> Draft | None:
        """Достаёт черновик и убирает его: повторное нажатие «Записать» ничего не запишет второй раз."""
        draft = self.get(number)
        self._items.pop(number, None)
        return draft

    def put(self, draft: Draft) -> None:
        self._items[draft.number] = draft

    def drop(self, number: int) -> None:
        self._items.pop(number, None)

    def _expire(self) -> None:
        now = self._clock()
        for number in [n for n, d in self._items.items() if now - d.created > DRAFT_TTL]:
            del self._items[number]


class SavedBox:
    """Записанные ботом операции — для кнопки «↩️ Отменить» (сутки, в памяти)."""

    def __init__(self, clock: Any = time.time) -> None:
        self._clock = clock
        self._items: dict[int, tuple[Draft, float]] = {}

    def add(self, draft: Draft) -> None:
        now = self._clock()
        self._items = {n: v for n, v in self._items.items() if now - v[1] <= UNDO_TTL}
        self._items[draft.number] = (draft, now)

    def take(self, number: int) -> Draft | None:
        entry = self._items.pop(number, None)
        if entry is None or self._clock() - entry[1] > UNDO_TTL:
            return None
        return entry[0]

    def put_back(self, draft: Draft) -> None:
        self._items[draft.number] = (draft, self._clock())


def _key(note: str) -> str:
    return " ".join(note.casefold().split())[:60]


def guess_category(note: str, categories: Sequence[Category], learned: dict[str, str], *, income: bool) -> str:
    """id категории: сначала то, что вы выбирали раньше, потом совпадение с названием; иначе пусто."""
    pool = {c.id: c for c in categories if c.is_income == income}
    remembered = learned.get(_key(note))
    if remembered in pool:
        return remembered
    for category in pool.values():
        if note and (matches(note, category.name) or matches(category.name, note)):
            return category.id
    return ""


async def remember_category(store: Any, note: str, category_id: str) -> None:
    key = _key(note)
    if not key or not category_id:
        return
    learned = dict(store.get(KEY_WORDS) or {})
    learned.pop(key, None)  # переносим в конец, чтобы старые слова вымывались первыми
    learned[key] = category_id
    if len(learned) > MAX_WORDS_MEMORY:
        learned = dict(list(learned.items())[-MAX_WORDS_MEMORY:])
    await store.set(KEY_WORDS, learned)


async def persist_saved(store: Any, box: SavedBox) -> None:
    if store is None:
        return
    data = {
        str(num): {
            "cents": d.parsed.cents,
            "note": d.parsed.note,
            "day": d.day,
            "category_id": d.category_id,
            "created": d.created,
            "account_id": d.account_id,
            "saved_at": ts,
        }
        for num, (d, ts) in list(box._items.items())[-MAX_DRAFTS:]
    }
    await store.set(KEY_SAVED, data)


def restore_saved(store: Any, box: SavedBox) -> None:
    if store is None:
        return
    raw = store.get(KEY_SAVED)
    if not isinstance(raw, dict):
        return
    now = box._clock()
    for key, val in raw.items():
        if not str(key).isdigit() or not isinstance(val, dict):
            continue
        num = int(key)
        if num in box._items:
            continue
        saved_at = float(val.get("saved_at") or 0.0)
        if now - saved_at > UNDO_TTL:
            continue
        cents = int(val.get("cents") or 0)
        if not cents:
            continue
        draft = Draft(
            number=num,
            parsed=ParsedExpense(cents, str(val.get("note") or "")),
            day=str(val.get("day") or ""),
            category_id=str(val.get("category_id") or ""),
            created=float(val.get("created") or saved_at),
            account_id=str(val.get("account_id") or ""),
        )
        box._items[num] = (draft, saved_at)
