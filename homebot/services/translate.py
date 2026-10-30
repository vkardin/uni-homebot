"""Перевод заголовков новостей на русский через Ollama — одним запросом на всю пачку."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

log = logging.getLogger(__name__)

PROMPT = (
    "Переведи на русский язык заголовки новостей. Верни ТОЛЬКО пронумерованный список в том же порядке, "
    "строго в формате «N. перевод», по одному заголовку на строку, без пояснений. "
    "Названия продуктов, игр, фильмов и компаний не переводи."
)
_NUMBERED_RE = re.compile(r"^\s*(\d{1,3})[.)]\s*(.+?)\s*$")
_LATIN_RE = re.compile(r"[A-Za-z]")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]")


ModelChoice = str | Callable[[], str]  # имя модели или функция, которая его вернёт (выбор в настройках бота)


def resolve_model(choice: ModelChoice | None) -> str:
    if callable(choice):
        return choice() or ""
    return choice or ""


def ask_model(ollama: Any, system: str, text: str, choice: ModelChoice | None) -> Awaitable[str]:
    """Запрос к модели перевода; пустой выбор — модель чата (параметр model не передаём)."""
    model = resolve_model(choice)
    return ollama.ask(system, text, model=model) if model else ollama.ask(system, text)


class _Asker(Protocol):
    async def ask(self, system: str, text: str) -> str: ...


def parse_numbered(text: str, count: int) -> dict[int, str]:
    """`1. перевод` -> {0: 'перевод'}; номера вне диапазона и пустые строки игнорируются."""
    result: dict[int, str] = {}
    for line in text.splitlines():
        match = _NUMBERED_RE.match(line)
        if not match:
            continue
        index = int(match[1]) - 1
        if 0 <= index < count and index not in result and match[2].strip():
            result[index] = match[2].strip().strip('«»"')
    return result
