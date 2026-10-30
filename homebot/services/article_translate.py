"""🌐 Перевод текстов новостей (заголовки, описания) на русский.

Два движка:
* Ollama (по умолчанию) — ваша локальная модель. На процессоре это небыстро (≈4 токена/с), поэтому переводим
  пачками с фоновым приоритетом (ваш чат важнее) и только начало длинного текста (`max_chars`).
* LibreTranslate — отдельный локальный переводчик в Docker: быстрее и без нагрузки на модель, переводит целиком.

Русский текст не переводим; украинский — переводим, если TRANSLATE_UKRAINIAN=true (по умолчанию).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import aiohttp

from homebot.services.lang import is_russian
from homebot.services.ollama import OllamaError, OllamaPreempted
from homebot.services.translate import ModelChoice, ask_model, parse_numbered

log = logging.getLogger(__name__)

PROMPT = (
    "Переведи на русский язык каждый пронумерованный абзац статьи. Верни ТОЛЬКО пронумерованный список в том же порядке, "
    "строго в формате «N. перевод», каждый абзац — одной строкой, без пояснений и без сокращений. "
    "Имена людей, названия компаний, продуктов, фильмов и игр оставляй узнаваемыми."
)
CHUNK_CHARS = 1500
CHUNK_TIMEOUT = 420
PREEMPT_RETRIES = 4

Translate = Callable[[Sequence[str]], Awaitable[list[str | None]]]


def looks_russian(text: str) -> bool:
    return is_russian(text)


def chunks(paragraphs: Sequence[str], limit: int = CHUNK_CHARS) -> list[list[int]]:
    """Индексы абзацев, сгруппированные так, чтобы в запросе было не больше `limit` символов."""
    groups: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, text in enumerate(paragraphs):
        if current and size + len(text) > limit:
            groups.append(current)
            current, size = [], 0
        current.append(index)
        size += len(text)
    if current:
        groups.append(current)
    return groups


class OllamaArticleTranslator:
    def __init__(self, ollama: Any, *, max_chars: int = 6000, model: ModelChoice = "") -> None:
        self._ollama = ollama
        self._model = model  # лёгкая модель для перевода (пусто — активная модель чата)
        self._max_chars = max_chars

    async def __call__(self, paragraphs: Sequence[str]) -> list[str | None]:
        result: list[str | None] = [None] * len(paragraphs)
        budget = self._max_chars
        allowed: list[int] = []
        for index, text in enumerate(paragraphs):
            if budget <= 0:
                break
            allowed.append(index)
            budget -= len(text)
        for group in chunks([paragraphs[i] for i in allowed]):
            indexes = [allowed[i] for i in group]
            numbered = "\n".join(f"{n}. {' '.join(paragraphs[i].split())}" for n, i in enumerate(indexes, 1))
            answer = await self._ask(numbered)
            if answer is None:
                break  # модель недоступна — остальное оставим оригиналом
            for position, text in parse_numbered(answer, len(indexes)).items():
                if looks_russian(text):
                    result[indexes[position]] = text
        return result

    async def _ask(self, numbered: str) -> str | None:
        for attempt in range(PREEMPT_RETRIES):
            try:
                return await asyncio.wait_for(ask_model(self._ollama, PROMPT, numbered, self._model), CHUNK_TIMEOUT)
            except OllamaPreempted:
                await asyncio.sleep(30 * (attempt + 1))  # модель занята вашим запросом — подождём и продолжим
            except (OllamaError, TimeoutError) as exc:
                log.warning("Article translation failed: %s", exc)
                return None
        return None


class LibreTranslator:
    """LibreTranslate: POST /translate с массивом строк — один запрос на всю статью."""

    def __init__(self, session: aiohttp.ClientSession, url: str, api_key: str = "") -> None:
        self._session = session
        self._url = url.rstrip("/") + "/translate"
        self._key = api_key

    async def __call__(self, paragraphs: Sequence[str]) -> list[str | None]:
        result: list[str | None] = [None] * len(paragraphs)
        for group in chunks(paragraphs, 5000):
            body: dict[str, Any] = {"q": [paragraphs[i] for i in group], "source": "auto", "target": "ru", "format": "text"}
            if self._key:
                body["api_key"] = self._key
            try:
                async with self._session.post(self._url, json=body, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    data = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                log.warning("LibreTranslate failed: %s", exc)
                break
            translated = data.get("translatedText") if isinstance(data, dict) else None
            if isinstance(translated, str):
                translated = [translated]
            if not isinstance(translated, list):
                log.warning("LibreTranslate: unexpected answer: %s", str(data)[:200])
                break
            for i, text in zip(group, translated, strict=False):
                if isinstance(text, str) and text.strip():
                    result[i] = text.strip()
        return result
