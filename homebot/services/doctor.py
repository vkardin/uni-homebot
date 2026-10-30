"""🩺 Самопроверка: всё ли подключено и отвечает — одним экраном.

Каждая проверка идёт параллельно и с тайм-аутом: зависший сервис не задержит остальные. Здесь только логика
и данные (без aiogram), экран рисует `ui/extra_texts.render_doctor`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

OK, WARN, FAIL, OFF = "ok", "warn", "fail", "off"
TIMEOUT = 20


@dataclass(frozen=True, slots=True)
class Check:
    status: str
    name: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class FeedCheck:
    url: str
    title: str = ""
    items: int = 0
    error: str = ""

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or self.url


@dataclass(slots=True)
class Report:
    checks: list[Check] = field(default_factory=list)
    feeds: list[FeedCheck] = field(default_factory=list)
    off: list[str] = field(default_factory=list)  # не подключено (это не ошибка)
    took: float = 0.0

    @property
    def problems(self) -> int:
        return sum(1 for c in self.checks if c.status in (WARN, FAIL)) + sum(1 for f in self.feeds if f.error)


def short_error(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:160]


async def guard(name: str, check: Awaitable[Check], limit: float = TIMEOUT) -> Check:
    """Любая проверка: исключение или тайм-аут превращаются в понятную строку, а не роняют весь отчёт."""
    try:
        return await asyncio.wait_for(check, limit)
    except TimeoutError:
        return Check(FAIL, name, f"нет ответа за {limit:.0f} с")
    except Exception as exc:
        return Check(FAIL, name, short_error(exc))


async def guard_many(name: str, checks: Awaitable[list[Check]], limit: float = TIMEOUT) -> list[Check]:
    try:
        return await asyncio.wait_for(checks, limit)
    except TimeoutError:
        return [Check(FAIL, name, f"нет ответа за {limit:.0f} с")]
    except Exception as exc:
        return [Check(FAIL, name, short_error(exc))]


async def check_feed(url: str, fetch: Callable[[str], Awaitable[tuple[str, list[Any]]]]) -> FeedCheck:
    try:
        title, items = await asyncio.wait_for(fetch(url), TIMEOUT)
    except Exception as exc:
        return FeedCheck(url, error=short_error(exc) if not isinstance(exc, TimeoutError) else "нет ответа")
    return FeedCheck(url, title or "", len(items))


async def collect(checks: Sequence[Awaitable[Check | list[Check]]], feeds: Sequence[str], fetch: Any, off: Sequence[str]) -> Report:
    started = time.monotonic()
    feed_results = asyncio.gather(*(check_feed(url, fetch) for url in feeds)) if fetch is not None else asyncio.sleep(0, [])
    results, feed_list = await asyncio.gather(asyncio.gather(*checks), feed_results)
    flat: list[Check] = []
    for result in results:
        flat.extend(result if isinstance(result, list) else [result])
    return Report(flat, list(feed_list or []), list(off), time.monotonic() - started)


# ------------------------------------------------------------- типовые проверки
async def ollama_check(ollama: Any, *, news_model: str, translate_model: str, machine_url: str) -> list[Check]:
    models = await ollama.list_models()
    checks = [
        Check(
            OK if ollama.active_model in models else WARN,
            "Ollama",
            f"{len(models)} мод. · чат: {ollama.active_model}"
            + ("" if ollama.active_model in models else " — такой модели нет, выберите в 🤖 Модель ИИ"),
        )
    ]
    if news_model and news_model not in models:
        checks.append(Check(FAIL, "Модель новостей", f"{news_model} не найдена — ollama pull {news_model} или выберите другую"))
    if machine_url:
        checks.append(Check(OK, "Перевод", f"LibreTranslate ({machine_url})"))
    elif not translate_model:
        checks.append(Check(WARN, "Перевод", "переводит модель чата — лёгкая модель была бы быстрее (⚙️ → 🌐)"))
    elif translate_model not in models:
        checks.append(Check(FAIL, "Перевод", f"{translate_model} не найдена — ollama pull {translate_model}"))
    else:
        checks.append(Check(OK, "Перевод", translate_model))
    return checks
