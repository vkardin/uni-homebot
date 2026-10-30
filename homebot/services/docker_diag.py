"""Разбор проблем контейнеров: хвост журнала для сообщения и короткий диагноз от модели."""

from __future__ import annotations

import html
import re
from typing import Any, Protocol

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

DIAG_PROMPT = (
    "Ты — опытный администратор Linux и Docker. Ниже — проблема контейнера и конец его журнала. "
    "Ответь по-русски строго двумя короткими предложениями: 1) что, судя по журналу, пошло не так; "
    "2) что сделать (одно конкретное действие или команда). Используй только то, что видно в журнале. "
    "Если причина по журналу неясна — так и скажи, ничего не выдумывай."
)
DIAG_MAX_CHARS = 450


class Asker(Protocol):
    async def ask(self, system: str, text: str, *, model: str | None = None, schema: dict[str, Any] | None = None) -> str: ...


def clean_log(text: str) -> str:
    """Без цветовых кодов и управляющих символов — они ломают сообщение и сбивают модель."""
    return _CTRL_RE.sub("", _ANSI_RE.sub("", text))


def tail_lines(text: str, *, lines: int = 6, width: int = 150) -> str:
    """Последние непустые строки журнала, каждая не длиннее `width`."""
    rows = [row.rstrip() for row in clean_log(text).splitlines() if row.strip()]
    cut = [row if len(row) <= width else row[: width - 1] + "…" for row in rows[-lines:]]
    return "\n".join(cut)


def render_tail(text: str) -> str:
    """Блок <pre> для Telegram (HTML); пусто, если журнал пуст."""
    tail = tail_lines(text)
    return f"<pre>{html.escape(tail)}</pre>" if tail else ""


def prepare_diag_input(name: str, problem: str, logs: str, *, max_chars: int = 2500) -> str:
    body = clean_log(logs).strip()
    if len(body) > max_chars:
        body = "…" + body[-max_chars:]
    return f"Контейнер: {name}\nПроблема: {problem}\n\nКонец журнала:\n{body}"


def tidy_diag(answer: str) -> str:
    """Ответ модели одной короткой строкой текста без разметки."""
    text = " ".join(clean_log(answer).split())
    return text[: DIAG_MAX_CHARS - 1].rstrip() + "…" if len(text) > DIAG_MAX_CHARS else text
