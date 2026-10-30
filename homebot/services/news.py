"""Новости каждый час: только НОВЫЕ материалы за период, пересказанные локальной моделью.

Как отбираются новости:
1. Скачиваем все ленты параллельно (ошибка одной не мешает остальным).
2. Отбрасываем всё, что бот уже видел (таблица news_seen, ключ — guid/ссылка).
3. Новость с датой берём, только если она опубликована в окне (с прошлой рассылки). Новость без даты
   берём, только если ленту бот уже читал раньше: при первом знакомстве с лентой весь её архив
   просто запоминается и не вываливается в чат.
4. Убираем дубли (одинаковые заголовки из разных лент) и чередуем источники, чтобы пять новостей
   не пришли из одной ленты. Сортировка — свежие первыми.
5. Пересказ: один запрос к Ollama на всю пачку, ответ строго по JSON-схеме. Если у новости нет
   нормального описания, бот читает начало самой статьи — иначе модели пришлось бы додумывать.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlparse

import aiohttp

from homebot.services import feeds
from homebot.services.article_translate import OllamaArticleTranslator
from homebot.services.db import Database, SeenNews
from homebot.services.feeds import FeedParseError, NewsItem
from homebot.services.lang import is_russian, needs_russian
from homebot.services.ollama import OllamaError, OllamaPreempted, OllamaTimeout
from homebot.services.prefs import NewsPrefs
from homebot.services.translate import ModelChoice
from homebot.services.webtext import WebTextError, fetch_text

log = logging.getLogger(__name__)

_MAX_FEED_BYTES = 3 * 1024 * 1024
_FEED_TIMEOUT = aiohttp.ClientTimeout(total=15)
_ITEMS_PER_FEED = 40
_GRACE = timedelta(minutes=10)  # ленты часто публикуют с небольшой задержкой
_MIN_SUMMARY = 160  # короче — считаем, что описания нет, и читаем статью
_ARTICLE_CHARS = 1500
_ITEM_CHARS = 1000  # на одну новость в запросе к модели: на CPU каждая тысяча символов — это секунды
_RETELL_BUDGET = 600.0  # с: дольше пересказывать не ждём — остальное уйдёт с переведённым заголовком
_TITLES_TIMEOUT = 300.0  # с: на перевод пачки заголовков или описаний
TITLE_PROMPT = (
    "Переведи заголовок новости на русский язык. Названия компаний, игр, фильмов, песен и имена людей "
    "не переводи. Ответь только переведённым заголовком, без кавычек и пояснений."
)
_RETENTION_DAYS = 30
_NORM_RE = re.compile(r"[\W_]+", re.UNICODE)

RETELL_PROMPT = (
    "Ты — редактор новостной ленты. Для КАЖДОЙ новости из списка напиши по-русски:\n"
    "- title: короткий заголовок (до 90 символов) НА РУССКОМ: английский заголовок обязательно переведи;\n"
    "- summary: пересказ в 1–2 предложениях: что произошло и почему это важно.\n"
    "Пиши нейтрально, без оценок и вступлений. Используй ТОЛЬКО факты из текста новости, ничего не "
    "додумывай; если текста мало — перескажи только то, что есть. Названия компаний, игр, фильмов, "
    "песен и имена людей не переводи. Верни ответ строго по схеме, по одному объекту на каждую новость, "
    "с её номером n."
)
RETELL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["n", "title", "summary"],
            },
        }
    },
    "required": ["items"],
}


class _Asker(Protocol):
    async def ask(self, system: str, text: str, *, model: str | None = None, schema: dict[str, Any] | None = None) -> str: ...


_CYR_RE = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]")
_LAT_RE = re.compile(r"[A-Za-z]")
STRICT_RUSSIAN = "\n\nВАЖНО: поля title и summary — ТОЛЬКО на русском языке, даже если новость на английском."


def mostly_foreign(retold: dict[int, tuple[str, str]]) -> bool:
    return bool(retold) and sum(not is_russian(t + " " + s) for t, s in retold.values()) * 2 > len(retold)


@dataclass(frozen=True, slots=True)
class Candidate:
    item: NewsItem
    feed: str
    source: str


@dataclass(frozen=True, slots=True)
class Story:
    key: str
    title: str
    text: str
    link: str | None
    source: str
    published: datetime | None
    retold: bool


@dataclass(slots=True)
class NewsBatch:
    stories: list[Story]
    candidates: int
    errors: list[str]


def feed_name(url: str) -> str:
    host = urlparse(url).hostname or url
    return host.removeprefix("www.").removeprefix("feeds.")


def _normalize(title: str) -> str:
    return _NORM_RE.sub(" ", title.lower()).strip()


def select(candidates: Sequence[Candidate], count: int, *, now: datetime) -> list[Candidate]:
    """Свежие первыми, без дублей, с чередованием источников."""

    def moment(c: Candidate) -> datetime:
        return c.item.published or now

    unique: list[Candidate] = []
    seen_titles: set[str] = set()
    for candidate in sorted(candidates, key=moment, reverse=True):
        norm = _normalize(candidate.item.title)
        if norm in seen_titles:
            continue
        seen_titles.add(norm)
        unique.append(candidate)

    groups: OrderedDict[str, list[Candidate]] = OrderedDict()
    for candidate in unique:  # порядок групп — по самой свежей новости источника
        groups.setdefault(candidate.feed, []).append(candidate)

    picked: list[Candidate] = []
    while len(picked) < count and any(groups.values()):
        for queue in groups.values():
            if queue and len(picked) < count:
                picked.append(queue.pop(0))
    picked.sort(key=moment, reverse=True)
    return picked


def parse_retell(answer: str, count: int) -> dict[int, tuple[str, str]]:
    """JSON от модели → {индекс: (заголовок, пересказ)}. Мусор и чужие номера отбрасываются."""
    try:
        data = json.loads(answer)
    except ValueError:
        match = re.search(r"\{.*\}", answer, re.DOTALL)  # на случай текста вокруг JSON
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except ValueError:
            return {}
    items = data.get("items") if isinstance(data, dict) else data
    result: dict[int, tuple[str, str]] = {}
    for entry in items if isinstance(items, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("n")) - 1
        except (TypeError, ValueError):
            continue
        title = str(entry.get("title") or "").strip()
        summary = str(entry.get("summary") or "").strip()
        if 0 <= index < count and index not in result and title and summary:
            result[index] = (title[:200], summary[:600])
    return result


def _unquote(text: str) -> str:
    """Модель любит заключать перевод в кавычки целиком — снимаем только такие, внутренние «Apple» не трогаем."""
    text = text.strip()
    for left, right in (("«", "»"), ('"', '"'), ("'", "'")):
        if len(text) > 2 and text.startswith(left) and text.endswith(right) and text.count(left) == 1:
            return text[1:-1].strip()
    return text


def _fallback_text(item: NewsItem) -> str:
    text = item.summary or ""
    if len(text) > 280:
        text = text[:280].rsplit(" ", 1)[0] + "…"
    return text


class NewsService:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        db: Database,
        ollama: _Asker,
        *,
        translator: Callable[[Sequence[str]], Awaitable[list[str | None]]] | None = None,
        translate_model: ModelChoice = "",
    ) -> None:
        # 🌐 перевод заголовков и описаний: LibreTranslate, если подключён, иначе лёгкая модель (TRANSLATE_MODEL)
        self._translator = translator
        self._text_translator = translator or OllamaArticleTranslator(ollama, max_chars=20_000, model=translate_model)
        self._session = session
        self._db = db
        self._ollama = ollama
        # новости, о которых уже сообщила «слежка за темами», в часовую сводку второй раз не попадают
        self._lock = asyncio.Lock()  # кнопка «Свежие сейчас» и ежечасная рассылка не должны идти параллельно
        self._last_prune = 0.0

    # --------------------------------------------------------------- загрузка
    async def _download(self, url: str) -> bytes:
        async with self._session.get(
            url, timeout=_FEED_TIMEOUT, headers={"Accept": "application/rss+xml, application/atom+xml, */*;q=0.5"}
        ) as resp:
            resp.raise_for_status()
            buffer = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                buffer += chunk
                if len(buffer) > _MAX_FEED_BYTES:
                    cut = feeds.truncate_feed(bytes(buffer))  # берём только свежее начало
                    if cut is None:
                        raise ValueError("лента слишком большая")
                    return cut
            return bytes(buffer)

    async def fetch_feed(self, url: str) -> tuple[str, list[NewsItem]]:
        """(название, новости). Бросает FeedParseError/aiohttp.ClientError/TimeoutError/ValueError."""
        title, items = feeds.parse_feed(await self._download(url), _ITEMS_PER_FEED)
        return title or feed_name(url), items

    async def check_feed(self, url: str) -> tuple[bool, str]:
        """Проверка ленты перед добавлением: (ok, название или причина ошибки)."""
        try:
            title, items = await self.fetch_feed(url)
        except (aiohttp.ClientError, TimeoutError, FeedParseError, ValueError) as exc:
            if isinstance(exc, aiohttp.ClientResponseError):
                return False, f"сайт ответил HTTP {exc.status}"
            return False, str(exc)[:120] or type(exc).__name__
        if not items:
            return False, "в ленте нет записей"
        return True, title

    # ------------------------------------------------------------------- отбор
    async def collect(
        self, feed_urls: Sequence[str], *, now: datetime, window: timedelta
    ) -> tuple[list[Candidate], list[SeenNews], list[str]]:
        """(кандидаты, все новые-для-бота записи, ошибки лент)."""

        async def one(url: str) -> tuple[str, str, list[NewsItem], str | None]:
            try:
                title, items = await self.fetch_feed(url)
                return url, title, items, None
            except (aiohttp.ClientError, TimeoutError, FeedParseError, ValueError) as exc:
                reason = f"HTTP {exc.status}" if isinstance(exc, aiohttp.ClientResponseError) else type(exc).__name__
                log.warning("News: feed %s unavailable: %s", url, exc)
                return url, feed_name(url), [], f"{feed_name(url)}: {reason}"

        results = await asyncio.gather(*(one(url) for url in feed_urls))
        all_items = [(url, source, item) for url, source, items, _ in results for item in items]
        known = await self._db.known_news([item.key for _, _, item in all_items])
        familiar_feeds = await self._db.feeds_with_history(list(feed_urls))

        since = now - window - _GRACE
        candidates: list[Candidate] = []
        fresh: list[SeenNews] = []
        seen_keys: set[str] = set()
        for url, source, item in all_items:
            if item.key in known or item.key in seen_keys:
                continue
            seen_keys.add(item.key)
            fresh.append(SeenNews(item.key, url, item.title, item.link, int(item.published.timestamp()) if item.published else None))
            if item.published is not None:
                if since <= item.published <= now + timedelta(hours=1):
                    candidates.append(Candidate(item, url, source))
            elif url in familiar_feeds:
                candidates.append(Candidate(item, url, source))
        errors = [error for *_, error in results if error]
        return candidates, fresh, errors

    # ---------------------------------------------------------------- пересказ
    async def _article_text(self, candidate: Candidate) -> str:
        if not candidate.item.link:
            return ""
        try:
            _, text = await asyncio.wait_for(fetch_text(self._session, candidate.item.link), 20)
        except (WebTextError, TimeoutError) as exc:
            log.debug("News: cannot read article %s: %s", candidate.item.link, exc)
            return ""
        return text[:_ARTICLE_CHARS]

    async def _ask_retell(self, text: str, count: int, model: str | None) -> dict[int, tuple[str, str]]:
        """Пересказ с проверкой языка: если модель ответила не по-русски — одна повторная попытка построже.

        Ошибки Ollama пробрасываются: вызывающий решает, пробовать ли следующую новость.
        """
        retold: dict[int, tuple[str, str]] = {}
        for prompt in (RETELL_PROMPT, RETELL_PROMPT + STRICT_RUSSIAN):
            answer = await self._ollama.ask(prompt, text, model=model, schema=RETELL_SCHEMA)
            attempt = parse_retell(answer, count)
            if attempt and not mostly_foreign(attempt):
                return attempt
            retold = attempt or retold
            log.info("News retelling: model answered not in Russian or broke the format; retrying")
        # Не помогло: оставляем только русские пересказы, остальное — оригиналы.
        return {i: v for i, v in retold.items() if is_russian(v[0] + " " + v[1])}

    async def _batch_translate(self, texts: Sequence[str]) -> list[str | None]:
        """Перевод пачкой: LibreTranslate, если подключён, иначе лёгкая модель (TRANSLATE_MODEL).

        Ответ не по-русски отбрасывается — тогда останется оригинал.
        """
        if not texts:
            return []
        try:
            answers = await asyncio.wait_for(self._text_translator(list(texts)), _TITLES_TIMEOUT)
        except TimeoutError:
            log.warning("News translation timed out")
            return [None] * len(texts)
        except Exception as exc:  # перевод — удобство: без него новости всё равно уходят
            log.warning("News translation failed: %s", exc)
            return [None] * len(texts)
        padded = [*answers, *[None] * len(texts)][: len(texts)]
        return [_unquote(a) if a and is_russian(a) else None for a in padded]

    async def _translate_title(self, title: str, model: str | None = None) -> str | None:
        return (await self._batch_translate([title]))[0]

    async def _translate_titles(self, picked: Sequence[Candidate]) -> dict[int, str]:
        """Все иноязычные заголовки — одним запросом и ДО пересказа: даже если пересказ не успеет, заголовки будут по-русски."""
        todo = [i for i, c in enumerate(picked) if needs_russian(c.item.title)]
        answers = await self._batch_translate([picked[i].item.title for i in todo])
        titles = {i: a[:200] for i, a in zip(todo, answers, strict=True) if a}
        if todo:
            log.info("News: %s of %s titles translated", len(titles), len(todo))
        return titles

    async def _retell_each(self, picked: Sequence[Candidate], texts: Sequence[str], model: str | None) -> dict[int, tuple[str, str]]:
        """По одной новости за запрос: так модель на CPU успевает, а сбой одной не губит остальные."""
        started = time.monotonic()
        retold: dict[int, tuple[str, str]] = {}
        for index, (candidate, text) in enumerate(zip(picked, texts, strict=True)):
            if time.monotonic() - started > _RETELL_BUDGET:
                log.warning("News retelling: time budget exceeded after %s of %s stories", index, len(picked))
                break
            body = text[:_ITEM_CHARS] or "(нет текста, только заголовок)"
            block = f"[1] Источник: {candidate.source}\nЗаголовок: {candidate.item.title}\nТекст: {body}"
            try:
                one = await self._ask_retell(block, 1, model)
            except OllamaTimeout as exc:
                log.warning("News retelling timed out (%s): %s", candidate.item.title[:60], exc)
                continue
            except OllamaPreempted:
                log.info("News retelling paused: the model is answering the user")
                continue  # следующая новость встанет в очередь за вашим запросом
            except OllamaError as exc:
                log.warning("News retelling failed: %s", exc)
                break  # модель недоступна — дальше пробовать бессмысленно
            if 0 in one:
                retold[index] = one[0]
        log.info("News retelling: %s of %s retold in %.0f s", len(retold), len(picked), time.monotonic() - started)
        return retold

    async def _translate_plain(self, picked: Sequence[Candidate], retold: dict[int, tuple[str, str]]) -> dict[int, str]:
        """Описания новостей без пересказа (пересказ выключен или не успел) — переводом."""
        todo = [i for i, c in enumerate(picked) if i not in retold and needs_russian(_fallback_text(c.item))]
        answers = await self._batch_translate([_fallback_text(picked[i].item) for i in todo])
        return {i: a for i, a in zip(todo, answers, strict=True) if a}

    async def retell(self, picked: Sequence[Candidate], prefs: NewsPrefs) -> list[Story]:
        texts = [c.item.summary for c in picked]
        if prefs.retell and prefs.fetch_articles:
            need = [i for i, c in enumerate(picked) if len(c.item.summary) < _MIN_SUMMARY]
            articles = await asyncio.gather(*(self._article_text(picked[i]) for i in need))
            for index, article in zip(need, articles, strict=True):
                if len(article) > len(texts[index]):
                    texts[index] = article

        titles = await self._translate_titles(picked) if picked else {}
        retold: dict[int, tuple[str, str]] = {}
        if prefs.retell and picked:
            retold = await self._retell_each(picked, texts, prefs.model or None)
        for index, (title, text) in list(retold.items()):
            if not is_russian(title) and index in titles:
                retold[index] = (titles[index], text)  # пересказ по-русски, а заголовок модель оставила как был
        descriptions = await self._translate_plain(picked, retold) if picked else {}

        stories = []
        for index, candidate in enumerate(picked):
            default = (titles.get(index, candidate.item.title), descriptions.get(index, _fallback_text(candidate.item)))
            title, text = retold.get(index, default)
            stories.append(
                Story(
                    key=candidate.item.key,
                    title=title,
                    text=text,
                    link=candidate.item.link,
                    source=candidate.source,
                    published=candidate.item.published,
                    retold=index in retold,
                )
            )
        return stories

    # -------------------------------------------------------------------- цикл
    async def run(self, prefs: NewsPrefs, *, now: datetime | None = None, window: timedelta) -> NewsBatch:
        """Собрать, отобрать, пересказать. Всё увиденное помечается, отправленное — отдельно (mark_sent)."""
        moment = now or datetime.now(UTC)
        async with self._lock:
            candidates, fresh, errors = await self.collect(prefs.feeds, now=moment, window=window)
            picked = select(candidates, prefs.count, now=moment)
            stories = await self.retell(picked, prefs)
            await self._db.mark_news_seen(fresh, int(moment.timestamp()))
            await self._maybe_prune()
        return NewsBatch(stories=stories, candidates=len(candidates), errors=errors)

    async def translate_title(self, title: str, model: str | None = None) -> str | None:
        """Перевод заголовка на русский (для сообщений о темах); None — не вышло или уже по-русски."""
        if is_russian(title):
            return None
        return await self._translate_title(title, model)

    async def mark_sent(self, stories: Sequence[Story]) -> None:
        await self._db.mark_news_sent([(s.key, s.title, s.text) for s in stories], int(time.time()))

    async def _maybe_prune(self) -> None:
        if time.monotonic() - self._last_prune < 86400:
            return
        self._last_prune = time.monotonic()
        removed = await self._db.prune_news(int(time.time()) - _RETENTION_DAYS * 86400)
        if removed:
            log.info("Pruned %s old news records", removed)
