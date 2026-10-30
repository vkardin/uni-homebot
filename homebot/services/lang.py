"""Язык текста: русский ли он — и нужно ли его переводить.

Украинский по умолчанию тоже переводится на русский (`TRANSLATE_UKRAINIAN=true`): его выдают буквы і, ї, є, ґ,
которых в русском нет. Одно украинское имя в русском тексте («Укрзалізниця») перевод не включает.
"""

from __future__ import annotations

import re

_CYR_RE = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]")
_LAT_RE = re.compile(r"[A-Za-z]")
_UK_RE = re.compile(r"[ІіЇїЄєҐґ]")
_RU_RE = re.compile(r"[ЫыЭэЪъЁё]")

_settings = {"translate_ukrainian": True}


def set_translate_ukrainian(enabled: bool) -> None:
    _settings["translate_ukrainian"] = enabled


_UK_WORDS = frozenset(
    "з зі із та що щоб від які який яка яке також буде було були це ще як але або чи вже після під між його її їх "
    "році року років рік уряд україни україна".split()
)
_WORD_RE = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ']+")


def is_ukrainian(text: str) -> bool:
    """Украинские буквы (і, ї, є, ґ) и типично украинские слова; одно украинское имя в русском тексте не в счёт."""
    uk = len(_UK_RE.findall(text))
    cyr = len(_CYR_RE.findall(text))
    ru = len(_RU_RE.findall(text))  # ы, э, ъ, ё — их в украинском нет
    words = sum(1 for word in _WORD_RE.findall(text.lower()) if word in _UK_WORDS)
    if uk + words >= 2 and uk + 3 * words >= 0.03 * cyr and ru <= uk + words:
        return True
    return uk >= 1 and ru == 0 and cyr >= 15


def is_russian(text: str) -> bool:
    """Текст в основном кириллический и (если украинский переводим) не украинский. Латиница в названиях допустима."""
    if len(_CYR_RE.findall(text)) < len(_LAT_RE.findall(text)):
        return False
    return not (_settings["translate_ukrainian"] and is_ukrainian(text))


def needs_russian(text: str) -> bool:
    """Есть что переводить: в тексте хватает букв и он не русский."""
    letters = len(_CYR_RE.findall(text)) + len(_LAT_RE.findall(text))
    return letters >= 3 and not is_russian(text)
