"""Разбор короткой записи траты: «кофе 85», «такси 1 200», «+5000 зарплата».

Формат намеренно строгий, чтобы обычные сообщения не принимались за траты: в тексте ровно одно число
(в начале или в конце), остальное — короткое описание из 1–4 слов. Сумма хранится в КОПЕЙКАХ (целое),
как этого требует Actual Budget; расход — отрицательное число, доход — положительное.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_AMOUNT_CENTS = 100_000_000 * 100  # 100 млн — страховка от опечаток
MAX_WORDS = 4
MAX_TEXT_LEN = 80

_CURRENCY = r"(?:грн|гривен|гривны|₴|uah|uah\.|грн\.|г\.)"
_NUM = r"\d+(?:[  ]\d{3})*(?:[.,]\d{1,2})?"
_NUMBER = rf"(?P<sign>[+\-−–])?\s*(?P<num>{_NUM})\s*(?:{_CURRENCY})?"
_TAIL = re.compile(rf"^(?P<note>[^\d+\-−–][^\d]*?)\s+{_NUMBER}$", re.IGNORECASE)
_HEAD = re.compile(rf"^{_NUMBER}(?:\s+(?P<note>[^\d].*))?$", re.IGNORECASE)
_COMMAND_RE = re.compile(r"^/")


@dataclass(frozen=True, slots=True)
class ParsedExpense:
    cents: int  # <0 — расход, >0 — доход
    note: str  # описание без суммы

    @property
    def is_income(self) -> bool:
        return self.cents > 0


def to_cents(raw: str) -> int | None:
    """«1 200,50» -> 120050. None, если это не сумма."""
    cleaned = raw.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        whole, _, frac = cleaned.partition(".")
        cents = int(whole) * 100 + int((frac + "00")[:2] if frac else 0)
    except ValueError:
        return None
    return cents if 0 < cents <= MAX_AMOUNT_CENTS else None


def parse_expense(text: str, *, allow_bare: bool = False) -> ParsedExpense | None:
    """None — это не запись о трате (пусть обработает кто-то другой).

    `allow_bare` — режим «➖ Расход»: можно прислать просто сумму без описания.
    """
    text = " ".join((text or "").split())
    if not text or len(text) > MAX_TEXT_LEN or _COMMAND_RE.match(text):
        return None
    match = _TAIL.match(text) or _HEAD.match(text)
    if match is None:
        return None
    cents = to_cents(match.group("num"))
    if cents is None:
        return None
    note = (match.group("note") or "").strip(" ,.;:-")
    if (not note and not allow_bare) or len(note.split()) > MAX_WORDS:
        return None  # голое число — не трата (год, номер, ответ на вопрос)
    if "http://" in note or "https://" in note:
        return None
    income = match.group("sign") == "+"
    return ParsedExpense(cents if income else -cents, note)


def format_money(cents: int, currency: str = "₴", *, sign: bool = False) -> str:
    """12050 -> «120,50 ₴»; копейки показываем только если они есть."""
    value = abs(cents)
    whole, frac = divmod(value, 100)
    body = f"{whole:,}".replace(",", " ")
    if frac:
        body += f",{frac:02d}"
    prefix = ("−" if cents < 0 else "+") if sign and cents else ("−" if cents < 0 else "")
    return f"{prefix}{body} {currency}".strip()
