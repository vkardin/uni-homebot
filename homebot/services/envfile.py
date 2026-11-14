"""🔧 Правка .env из бота: убрать устаревшие ключи и записать новые значения — только с резервной копией.

* Перед каждой правкой копия .env кладётся в STATE_DIR/env-backups/ (права 600, хранится `KEEP` последних).
* Меняются только названные ключи; комментарии, порядок и остальные строки остаются как были.
* Файл переписывается на месте (а не через временный файл рядом): при обычной установке каталог /opt/homebot
  только для чтения, а сам .env разрешён на запись в homebot.service (`ReadWritePaths=`, с 10.3).
* В Docker .env не смонтирован в контейнер — там правка недоступна, бот подсказывает, что поменять руками.
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

BACKUP_DIR = "env-backups"
KEEP = 10
_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_KEY_RE = re.compile(r"[A-Z_][A-Z0-9_]*")

# Ключи функций, которые убраны из UNI (версия и чем заменены). Их можно удалять из .env без потерь.
LEGACY: tuple[tuple[str, str], ...] = (
    ("DIGEST_", "утренний дайджест убран в 3.2 — его заменяет часовая сводка"),
    ("RSS_ITEMS_PER_FEED", "убран в 3.2 — сводка сама решает, сколько новостей брать"),
    ("HEARTBEAT_", "старый «я жив» убран в 3.2 — сейчас это Сторож (WATCHDOG_*)"),
    ("AIR_", "воздушные тревоги (alerts.in.ua) убраны в 3.2"),
    ("ALERTS_IN_UA_TOKEN", "воздушные тревоги (alerts.in.ua) убраны в 3.2"),
    ("TOPICS_INTERVAL_MIN", "слежка за темами убрана в 3.2"),
    ("NEWS_INSTANT_VIEW", "Instant View убран в 3.2"),
)


@dataclass(frozen=True, slots=True)
class EnvKey:
    key: str
    legacy: str = ""  # почему ключ больше не нужен (пусто — не наш устаревший ключ)
    guess: str = ""  # похожий знакомый ключ: вероятная опечатка


def legacy_reason(key: str) -> str:
    for prefix, reason in LEGACY:
        if key == prefix or (prefix.endswith("_") and key.startswith(prefix)):
            return reason
    return ""


def classify(unknown: list[str], known: set[str]) -> list[EnvKey]:
    """Незнакомые ключи: устаревшие (можно убрать) и вероятные опечатки (с подсказкой)."""
    out = []
    for key in unknown:
        reason = legacy_reason(key)
        guess = "" if reason else next(iter(difflib.get_close_matches(key, sorted(known), n=1, cutoff=0.8)), "")
        out.append(EnvKey(key, reason, guess))
    return out


def writable(path: Path) -> bool:
    """Можно ли править .env (на файловой системе только для чтения access() тоже отвечает «нет»)."""
    return path.is_file() and os.access(path, os.R_OK | os.W_OK)


def backups(state_dir: Path) -> list[Path]:
    folder = state_dir / BACKUP_DIR
    if not folder.is_dir():
        return []
    return sorted(folder.glob(".env.*"), key=lambda p: p.name, reverse=True)


def backup(path: Path, state_dir: Path, *, now: float | None = None) -> Path:
    folder = state_dir / BACKUP_DIR
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now if now is not None else time.time()))
    target = folder / f".env.{stamp}"
    n = 1
    while target.exists():
        n += 1
        target = folder / f".env.{stamp}-{n}"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as dst, path.open("rb") as src:
        shutil.copyfileobj(src, dst)
    for old in backups(state_dir)[KEEP:]:
        old.unlink(missing_ok=True)
    return target


def _quote(value: str) -> str:
    if value == "" or re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    if "'" not in value:
        return f"'{value}'"  # одинарные кавычки: и systemd, и python-dotenv берут содержимое как есть
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _rewrite(path: Path, text: str) -> None:
    """На месте: файл тот же (права и владелец сохраняются), каталог может быть только для чтения."""
    with path.open("r+", encoding="utf-8") as fh:
        fh.write(text)
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())


def edit(path: Path, state_dir: Path, *, remove: list[str] | tuple[str, ...] = (), values: dict[str, str] | None = None) -> Path:
    """Убирает ключи `remove` и задаёт `values` (меняет строку на месте или дописывает в конец).

    Возвращает путь к резервной копии. OSError — если файл недоступен на запись (копия при этом не создаётся).
    """
    values = dict(values or {})
    for key in [*remove, *values]:
        if not _KEY_RE.fullmatch(key):
            raise ValueError(f"недопустимое имя ключа: {key}")
    for value in values.values():
        if "\n" in value or "\r" in value:
            raise ValueError("значение не может содержать перевод строки")
    if not writable(path):
        raise OSError(f"{path} недоступен на запись")
    original = path.read_text(encoding="utf-8")
    drop = {k.upper() for k in remove}
    pending = dict(values)
    lines: list[str] = []
    for line in original.splitlines():
        m = _LINE_RE.match(line)
        key = m.group(1).upper() if m else ""
        if key in drop:
            continue
        if key in pending:
            lines.append(f"{key}={_quote(pending.pop(key))}")
            continue
        if key in values:  # повтор того же ключа ниже по файлу — убираем, чтобы не перебил новое значение
            continue
        lines.append(line)
    if pending:
        lines += [f"{k}={_quote(v)}" for k, v in pending.items()]
    text = "\n".join(lines) + "\n"
    saved = backup(path, state_dir)
    _rewrite(path, text)
    return saved
