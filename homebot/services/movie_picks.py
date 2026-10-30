"""«Что посмотреть?»: подбор фильмов и сериалов локальной моделью + проверка каждого в Radarr/Sonarr.

Модель на 4B параметров иногда «придумывает» фильмы, поэтому в ответ попадает только то, что
действительно нашлось в TMDB, — с постером, годом и рейтингом оттуда, а не со слов модели.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from homebot.services.apps_http import AppError

KIND_MOVIE = "m"
KIND_SERIES = "s"

# ключ → (кнопка, запрос к модели, что ищем)
MOODS: dict[str, tuple[str, str, str]] = {
    "fun": ("😂 Посмеяться", "лёгкая смешная комедия на вечер, без тяжёлых тем", KIND_MOVIE),
    "thrill": ("🔪 Напряжённое", "захватывающий триллер или детектив, чтобы не оторваться", KIND_MOVIE),
    "scifi": ("🚀 Фантастика", "умная научная фантастика с хорошими спецэффектами", KIND_MOVIE),
    "action": ("💥 Боевик", "зрелищный боевик или приключения", KIND_MOVIE),
    "drama": ("🎭 Драма", "сильная драма, которая запомнится", KIND_MOVIE),
    "horror": ("👻 Ужасы", "по-настоящему страшный фильм ужасов", KIND_MOVIE),
    "anim": ("🧸 Мультфильм", "отличный анимационный фильм, который понравится и взрослым", KIND_MOVIE),
    "new": ("🆕 Новинки", "лучшие фильмы последних двух лет", KIND_MOVIE),
    "gem": ("💎 Удиви", "недооценённый, малоизвестный, но очень хороший фильм", KIND_MOVIE),
    "series": ("📺 Сериал", "сериал, который затягивает с первой серии", KIND_SERIES),
    "mini": ("⚡ Мини-сериал", "короткий мини-сериал на один-два вечера", KIND_SERIES),
    "sitcom": ("🛋 Ситком", "уютный смешной ситком, чтобы включать фоном", KIND_SERIES),
}

PICK_PROMPT = (
    "Ты — эксперт по кино и сериалам. Подбери {what} под запрос пользователя. Только реально существующие, "
    "никаких выдуманных. title — оригинальное название (обычно английское, как в IMDb), year — год выхода "
    "(для сериала — год первого сезона). why — одна короткая фраза ПО-РУССКИ, почему это подходит. "
    "Не повторяй то, что в списке «уже есть»."
)
_WHAT = {KIND_MOVIE: "полнометражные фильмы", KIND_SERIES: "сериалы"}

PICK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "movies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "year": {"type": "integer"}, "why": {"type": "string"}},
                "required": ["title", "year", "why"],
            },
        }
    },
    "required": ["movies"],
}

TITLE_PROMPT = (
    "Пользователь ищет фильм по русскому или украинскому названию. Верни оригинальное название фильма "
    "(как в IMDb) и год. Если не знаешь — верни пустое title."
)
TITLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "year": {"type": "integer"}},
    "required": ["title"],
}

_CYRILLIC = re.compile(r"[а-яёіїєґ]", re.IGNORECASE)


class _Asker(Protocol):
    async def ask(self, system: str, text: str, *, model: str | None = None, schema: dict[str, Any] | None = None) -> str: ...


class _Item(Protocol):
    @property
    def key(self) -> int: ...
    @property
    def year(self) -> int | None: ...


class _Lookup(Protocol):
    async def lookup(self, term: str) -> Sequence[Any]: ...


T = TypeVar("T", bound=_Item)


@dataclass(frozen=True, slots=True)
class Pick:
    title: str
    year: int | None
    why: str


def _json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match[0])
        except ValueError:
            return None


def parse_picks(text: str) -> list[Pick]:
    data = _json(text)
    items = data.get("movies") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    picks: list[Pick] = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            continue
        year = item.get("year")
        picks.append(
            Pick(
                title=str(item["title"]).strip(),
                year=year if isinstance(year, int) and 1900 < year < 2100 else None,
                why=str(item.get("why") or "").strip(),
            )
        )
    return picks


def best_match(films: Sequence[T], year: int | None) -> T | None:
    """Из результатов поиска — тот, у кого совпал год (±1: страны выпускают по-разному)."""
    if not films:
        return None
    if year is not None:
        for film in films:
            if film.year is not None and abs(film.year - year) <= 1:
                return film
        return None  # год не совпал — скорее всего, модель перепутала фильм
    return films[0]


def is_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC.search(text))


async def translate_title(asker: _Asker, query: str, *, model: str | None = None) -> str | None:
    """«Дюна» → «Dune 2021»: оригинальное название для поиска. None — модель не знает или недоступна."""
    try:
        data = _json(await asker.ask(TITLE_PROMPT, query, model=model, schema=TITLE_SCHEMA))
    except Exception:  # модель — лишь подсказка; её сбой не должен ломать поиск
        return None
    if not isinstance(data, dict):
        return None
    title = str(data.get("title") or "").strip()
    if not title:
        return None
    year = data.get("year") if isinstance(data.get("year"), int) else None
    return f"{title} {year}" if year else title


async def recommend(
    radarr: _Lookup,
    asker: _Asker,
    request: str,
    *,
    count: int = 5,
    exclude: tuple[str, ...] = (),
    model: str | None = None,
    kind: str = KIND_MOVIE,
) -> list[tuple[Any, str]]:
    """Фильмы/сериалы под настроение: модель предлагает с запасом, остаются только найденные в базе."""
    system = PICK_PROMPT.format(what=_WHAT.get(kind, _WHAT[KIND_MOVIE]))
    prompt = f"Запрос: {request}\nНужно фильмов: {count + 2}"
    if exclude:
        prompt += "\nУже есть (не предлагай): " + "; ".join(exclude[:40])
    picks = parse_picks(await asker.ask(system, prompt, model=model, schema=PICK_SCHEMA))
    if not picks:
        raise AppError("модель ничего не предложила — попробуйте ещё раз")

    async def resolve(pick: Pick) -> Any | None:
        try:
            films = await radarr.lookup(f"{pick.title} {pick.year}" if pick.year else pick.title)
        except AppError:
            return None
        return best_match(films, pick.year)

    resolved = await asyncio.gather(*(resolve(p) for p in picks[: count + 2]))
    result: list[tuple[Any, str]] = []
    seen: set[int] = set()
    for pick, film in zip(picks, resolved, strict=False):
        if film is None or film.key in seen:
            continue
        seen.add(film.key)
        result.append((film, pick.why))
        if len(result) >= count:
            break
    return result
