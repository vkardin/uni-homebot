"""🎙 Распознавание речи на CPU (faster-whisper). Пакет необязательный: без него бот объясняет, как его поставить.

Модель скачивается один раз в `<STATE_DIR>/models` при первом голосовом (base ≈ 150 МБ) и дальше работает локально.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

INSTALL_HINT = (
    "Для голосовых нужен пакет распознавания (ставится один раз):\n"
    "<code>sudo -u homebot /opt/homebot/.venv/bin/pip install -r /opt/homebot/requirements-voice.txt</code>\n"
    "<code>sudo systemctl restart homebot</code>"
)


class VoiceUnavailable(RuntimeError):
    """Пакет не установлен."""


class VoiceError(RuntimeError):
    """Не удалось распознать. Текст безопасно показывать."""


_lock = threading.Lock()
_models: dict[tuple[str, str], Any] = {}


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _load(model: str, root: Path) -> Any:
    with _lock:
        key = (model, str(root))
        if key not in _models:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise VoiceUnavailable(INSTALL_HINT) from exc
            root.mkdir(parents=True, exist_ok=True)
            log.info("Loading speech model %s (first run downloads it)", model)
            _models[key] = WhisperModel(model, device="cpu", compute_type="int8", download_root=str(root))
        return _models[key]


def _transcribe_sync(path: Path, model: str, language: str, root: Path) -> str:
    engine = _load(model, root)
    try:
        segments, _ = engine.transcribe(str(path), language=language or None, vad_filter=True, beam_size=1)
        return " ".join(segment.text.strip() for segment in segments).strip()
    except VoiceUnavailable:
        raise
    except Exception as exc:
        raise VoiceError(f"не получилось распознать: {exc}") from exc


async def transcribe(path: Path, *, model: str, language: str, root: Path) -> str:
    return await asyncio.to_thread(_transcribe_sync, path, model, language, root)


class Drafts:
    """Распознанный текст ждёт выбора кнопкой; номер идёт в callback-данные."""

    MAX = 20

    def __init__(self) -> None:
        self._items: dict[int, str] = {}
        self._next = 0

    def put(self, text: str, base: int) -> int:
        if self._next == 0:
            self._next = base
        self._next += 1
        self._items[self._next] = text
        while len(self._items) > self.MAX:
            self._items.pop(next(iter(self._items)))
        return self._next

    def get(self, ref: int) -> str | None:
        return self._items.get(ref)

    def drop(self, ref: int) -> None:
        self._items.pop(ref, None)
