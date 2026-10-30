"""Поиск слов в тексте по началу слова: «Кривой Рог» найдёт «в Кривом Роге».

Слово подходит, если текст содержит ВСЕ слова запроса. Совпадение идёт по НАЧАЛУ слова (русские окончания не мешают),
слова с цифрами и короткие слова ищутся точно. Нейросеть не нужна — это обычный поиск.
"""

from __future__ import annotations

import re
from functools import lru_cache

_SPLIT_RE = re.compile(r"[^\w]+", re.UNICODE)
_STEM_CUT = 2  # сколько букв с конца отбрасываем у длинных слов (окончания)


def _tokens(topic: str) -> list[str]:
    return [t for t in _SPLIT_RE.split(topic.casefold()) if t]


def _word_pattern(token: str) -> str:
    if any(ch.isdigit() for ch in token):
        return re.escape(token) + r"(?!\w)"  # «5070», «rtx5070» — только точное слово
    if len(token) <= 3:
        return re.escape(token) + r"\w{0,2}(?!\w)"  # «рог» найдёт «рога», «рогу», но не «рогатый»
    return re.escape(token[: max(4, len(token) - _STEM_CUT)]) + r"\w*"  # начало слова: окончания не мешают


@lru_cache(maxsize=256)
def _patterns(topic: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(r"(?<!\w)" + _word_pattern(token), re.UNICODE) for token in _tokens(topic))


def matches(text: str, topic: str) -> bool:
    """Все слова запроса есть в тексте (по началу слова)."""
    patterns = _patterns(topic)
    if not patterns:
        return False
    haystack = text.casefold()
    return all(p.search(haystack) for p in patterns)
