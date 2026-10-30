"""Клиент локальной Ollama (REST API).

Ключевые решения:
* один общий `aiohttp.ClientSession` (создаётся в `main`), таймаут задаётся на каждый запрос;
* генерации идут по одной через очередь с приоритетами (`llm_queue`): ваш запрос — первым, фоновые
  (новости, переводы) — последними и прерываются, если вы ждёте ответа;
* все сетевые сбои превращаются в `OllamaError` с понятным текстом для пользователя;
* выгрузка модели из памяти — `keep_alive: 0` (см. `unload`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

from homebot.services.llm_queue import PriorityGate, Slot
from homebot.services.state import StateStore

log = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)  # блоки рассуждений deepseek-r1, qwen3 и т.п.
_MAX_INPUT_CHARS = 12_000
_STATE_KEY_MODEL = "ollama.model"

_SUMMARY_PROMPTS = {
    "text": ("Сделай краткое резюме присланного текста на русском языке: 3–6 коротких пунктов, только суть, без вступлений и оценок."),
    "news": (
        "Ниже заголовки новостей из разных источников. Выдели 3–5 главных тем, сгруппируй "
        "похожие, пиши кратко и по-русски, без вступлений."
    ),
}


class OllamaError(Exception):
    """Базовая ошибка работы с Ollama. Текст безопасно показывать пользователю."""


class OllamaUnavailable(OllamaError):
    """Ollama не запущена или недоступна по сети."""


class OllamaTimeout(OllamaError):
    """Ollama не ответила за отведённое время."""


class OllamaAPIError(OllamaError):
    """Ollama ответила ошибкой (нет модели, неверный запрос, неожиданный формат)."""


class OllamaPreempted(OllamaError):
    """Фоновый запрос прерван: модель понадобилась вам (см. llm_queue)."""


@dataclass(frozen=True, slots=True)
class LoadedModel:
    name: str
    size: int
    size_vram: int
    expires_at: datetime | None


def normalize_keep_alive(value: str) -> str | int:
    """'5m' -> '5m', '0' -> 0, '-1' -> -1 (Ollama принимает и строки-длительности, и числа)."""
    value = value.strip()
    return int(value) if re.fullmatch(r"-?\d+", value) else value


def strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def visible_text(buffer: str) -> tuple[str, bool]:
    """Что показать во время потоковой генерации: (текст без рассуждений, модель ещё «думает»)."""
    text = _THINK_RE.sub("", buffer)
    open_at = text.find("<think>")
    if open_at != -1:
        return text[:open_at].strip(), True
    return text.strip(), False


def _parse_dt(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


class OllamaService:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        store: StateStore,
        *,
        host: str,
        default_model: str,
        request_timeout: float,
        keep_alive: str,
        think: bool | None = False,
        max_history: int,
        system_prompt: str,
    ) -> None:
        self._session = session
        self._store = store
        self._host = host.rstrip("/")
        self._default_model = default_model
        self._timeout = request_timeout
        self._keep_alive = normalize_keep_alive(keep_alive)
        # False — модель отвечает сразу, без «размышлений» (на CPU они занимают минуты); None — не указывать.
        self._think = think
        self._no_think_support: set[str] = set()  # модели, которые отвергли параметр think
        self._system_prompt = system_prompt
        self.max_history = max_history
        self._gate = PriorityGate()

    # ----------------------------------------------------------- свойства
    @property
    def host(self) -> str:
        return self._host

    @property
    def gate(self) -> PriorityGate:
        return self._gate

    @property
    def busy(self) -> bool:
        """Модель сейчас считает (чей-то запрос держит очередь): процессор занят ею, и это ожидаемо."""
        return self._gate.holder is not None

    @property
    def active_model(self) -> str:
        value = self._store.get(_STATE_KEY_MODEL)
        return value if isinstance(value, str) and value else self._default_model

    async def set_active_model(self, name: str) -> None:
        await self._store.set(_STATE_KEY_MODEL, name)

    # ----------------------------------------------------------- запросы
    async def list_models(self) -> list[str]:
        data = await self._request("GET", "/api/tags", total_timeout=10)
        return sorted(m["name"] for m in data.get("models", []) if isinstance(m, dict) and "name" in m)

    async def loaded_models(self) -> list[LoadedModel]:
        """Модели, которые сейчас занимают память (`/api/ps`)."""
        data = await self._request("GET", "/api/ps", total_timeout=10)
        result: list[LoadedModel] = []
        for item in data.get("models", []):
            if not isinstance(item, dict) or "name" not in item:
                continue
            result.append(
                LoadedModel(
                    name=item["name"],
                    size=int(item.get("size") or 0),
                    size_vram=int(item.get("size_vram") or 0),
                    expires_at=_parse_dt(item.get("expires_at")),
                )
            )
        return result

    def trim_history(self, history: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
        return [dict(m) for m in history[-self.max_history :]]

    async def chat(self, history: Sequence[Mapping[str, str]], *, model: str | None = None) -> str:
        """Ответ модели на историю диалога (`[{"role": "user", "content": "..."}, ...]`)."""
        messages = [{"role": "system", "content": self._system_prompt}, *self.trim_history(history)]
        return await self._complete(messages, model)

    async def ask(self, system: str, text: str, *, model: str | None = None, schema: dict[str, Any] | None = None) -> str:
        """Одиночный запрос с собственным системным промптом (переводы, разбор логов).

        `schema` — JSON-схема ответа (structured outputs Ollama): модель обязана вернуть валидный JSON.
        """
        if len(text) > _MAX_INPUT_CHARS:
            text = text[:_MAX_INPUT_CHARS] + "\n[текст обрезан]"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": text}]
        return await self._complete(messages, model, fmt=schema)

    async def chat_stream(self, history: Sequence[Mapping[str, str]], *, model: str | None = None) -> AsyncIterator[str]:
        """Потоковый ответ: отдаёт фрагменты текста по мере генерации.

        Используйте через `contextlib.aclosing`, чтобы при обрыве блокировка генерации освободилась сразу.
        """
        payload: dict[str, Any] = {
            "model": model or self.active_model,
            "messages": [{"role": "system", "content": self._system_prompt}, *self.trim_history(history)],
            "stream": True,
            "keep_alive": self._keep_alive,
        }
        self._apply_think(payload)
        url = f"{self._host}/api/chat"
        # total=None: длинный ответ может идти минутами; ограничиваем паузу между фрагментами.
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=self._timeout)
        slot = await self._gate.acquire()
        try:
            try:
                async with self._session.post(url, json=payload, timeout=timeout) as resp:
                    if resp.status >= 400:
                        error = _describe_http_error(resp.status, await resp.text())
                        if self._think_rejected(payload, error):
                            # Модель не поддерживает параметр think — повторяем запрос без него.
                            payload.pop("think", None)
                            async with self._session.post(url, json=payload, timeout=timeout) as retry:
                                if retry.status >= 400:
                                    raise OllamaAPIError(_describe_http_error(retry.status, await retry.text()))
                                async for piece in _iter_stream(retry):
                                    yield piece
                                return
                        raise OllamaAPIError(error)
                    async for piece in _iter_stream(resp):
                        yield piece
            except OllamaError:
                raise
            except TimeoutError as exc:
                raise OllamaTimeout(f"нет новых данных {self._timeout:.0f} с") from exc
            except aiohttp.ClientConnectionError as exc:
                raise OllamaUnavailable(f"не удалось подключиться к {self._host}") from exc
            except aiohttp.ClientError as exc:
                raise OllamaAPIError(f"ошибка HTTP-клиента: {exc}") from exc
        finally:
            self._gate.release(slot)

    async def summarize(self, text: str, *, kind: str = "text", model: str | None = None) -> str:
        """Краткое резюме текста. `kind`: 'text' (произвольный текст) или 'news' (заголовки)."""
        if len(text) > _MAX_INPUT_CHARS:
            text = text[:_MAX_INPUT_CHARS] + "\n[текст обрезан]"
        messages = [
            {"role": "system", "content": _SUMMARY_PROMPTS.get(kind, _SUMMARY_PROMPTS["text"])},
            {"role": "user", "content": text},
        ]
        return await self._complete(messages, model)

    async def unload(self, model: str | None = None) -> list[str]:
        """Выгружает модель(и) из памяти запросом с `keep_alive: 0`.

        Без аргумента выгружает все загруженные сейчас модели (список из `/api/ps`).
        Возвращает имена выгруженных моделей; пустой список — выгружать было нечего.
        """
        names = [model] if model else [m.name for m in await self.loaded_models()]
        unloaded: list[str] = []
        for name in names:
            await self._request("POST", "/api/generate", json={"model": name, "keep_alive": 0}, total_timeout=30)
            log.info("Ollama model unloaded: %s", name)
            unloaded.append(name)
        return unloaded

    # --------------------------------------------------------- внутренности
    def _apply_think(self, payload: dict[str, Any]) -> None:
        if self._think is not None and payload["model"] not in self._no_think_support:
            payload["think"] = self._think

    def _think_rejected(self, payload: dict[str, Any], error: str) -> bool:
        """Старые/не «думающие» модели отвечают 400 «does not support thinking» — запоминаем и не шлём think."""
        if "think" in payload and "think" in error.lower():
            self._no_think_support.add(payload["model"])
            return True
        return False

    async def _complete(self, messages: list[dict[str, str]], model: str | None, *, fmt: dict[str, Any] | None = None) -> str:
        payload: dict[str, Any] = {
            "model": model or self.active_model,
            "messages": messages,
            "stream": False,
            "keep_alive": self._keep_alive,
        }
        if fmt is not None:
            payload["format"] = fmt
        self._apply_think(payload)
        slot = await self._gate.acquire()
        try:
            data = await self._preemptable(slot, self._chat_once(payload))
        finally:
            self._gate.release(slot)

        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise OllamaAPIError("неожиданный формат ответа Ollama")
        if fmt is not None:
            return strip_think(content)
        return strip_think(content) or "(пустой ответ)"

    async def _chat_once(self, payload: dict[str, Any]) -> Any:
        try:
            return await self._request("POST", "/api/chat", json=payload, total_timeout=self._timeout)
        except OllamaAPIError as exc:
            if not self._think_rejected(payload, str(exc)):
                raise
            payload.pop("think", None)
            return await self._request("POST", "/api/chat", json=payload, total_timeout=self._timeout)

    @staticmethod
    async def _preemptable(slot: Slot, work: Any) -> Any:
        """Запрос идёт отдельной задачей, чтобы очередь могла прервать именно его, а не всю фоновую работу."""
        inner = asyncio.ensure_future(work)
        slot.task = inner
        try:
            return await inner
        except asyncio.CancelledError:
            me = asyncio.current_task()
            if slot.preempted and (me is None or not me.cancelling()):
                raise OllamaPreempted("модель понадобилась для вашего запроса — фоновая работа подождёт") from None
            raise
        finally:
            slot.task = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        total_timeout: float = 10,
    ) -> Any:
        url = f"{self._host}{path}"
        try:
            async with self._session.request(method, url, json=json, timeout=aiohttp.ClientTimeout(total=total_timeout)) as resp:
                if resp.status >= 400:
                    raise OllamaAPIError(_describe_http_error(resp.status, await resp.text()))
                return await resp.json(content_type=None)
        except OllamaError:
            raise
        except TimeoutError as exc:
            raise OllamaTimeout(f"нет ответа за {total_timeout:.0f} с") from exc
        except aiohttp.ClientConnectionError as exc:
            raise OllamaUnavailable(f"не удалось подключиться к {self._host}") from exc
        except aiohttp.ClientError as exc:
            raise OllamaAPIError(f"ошибка HTTP-клиента: {exc}") from exc
        except ValueError as exc:  # json.JSONDecodeError
            raise OllamaAPIError("Ollama вернула некорректный JSON") from exc


async def _iter_stream(resp: Any) -> AsyncIterator[str]:
    """Разбор NDJSON-потока /api/chat: отдаёт фрагменты текста до признака done."""
    async for raw in resp.content:
        line = raw.strip()
        if not line:
            continue
        try:
            chunk = json.loads(line)
        except ValueError as exc:
            raise OllamaAPIError("Ollama вернула некорректный поток") from exc
        if not isinstance(chunk, dict):
            continue
        if chunk.get("error"):
            raise OllamaAPIError(str(chunk["error"])[:200])
        message = chunk.get("message")
        piece = message.get("content") if isinstance(message, dict) else None
        if isinstance(piece, str) and piece:
            yield piece
        if chunk.get("done"):
            return


def _describe_http_error(status: int, body: str) -> str:
    """Достаёт понятное сообщение из тела ошибки Ollama (`{"error": "..."}`)."""
    message = ""
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            message = str(parsed.get("error", ""))
    except ValueError:
        message = body
    message = " ".join(message.split())[:200]
    return f"HTTP {status}: {message}" if message else f"HTTP {status}"
