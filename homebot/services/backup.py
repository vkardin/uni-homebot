"""💾 Бэкап: база SQLite + состояние (state.json) + выбранные файлы/папки (например, compose-файлы Docker).

Архив `homebot-YYYYmmdd-HHMMSS.tar.gz` лежит в `<STATE_DIR>/backups/`, хранится последние N штук.
`.env` в бэкап не попадает (там токены). Восстанавливаются база и состояние; дополнительные файлы лежат в архиве
в папке `extra/` — их вы достаёте руками, бот чужие конфиги не перезаписывает.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any

from homebot.services.db import Database
from homebot.services.state import StateStore

log = logging.getLogger(__name__)

STATE_KEY = "backup"
DB_NAME = "homebot.db"
STATE_NAME = "state.json"
MAX_EXTRA_FILE = 5 * 1024 * 1024
MAX_EXTRA_TOTAL = 50 * 1024 * 1024
_NAME_RE = re.compile(r"^homebot-(\d{8}-\d{6})\.tar\.gz$")


class BackupError(RuntimeError):
    """Текст безопасно показывать пользователю."""


@dataclass(frozen=True, slots=True)
class BackupFile:
    path: Path
    ts: int
    size: int

    @property
    def name(self) -> str:
        return self.path.name


def backup_dir(state_dir: Path) -> Path:
    return state_dir / "backups"


def list_backups(state_dir: Path) -> list[BackupFile]:
    """Новые — первыми."""
    found: list[BackupFile] = []
    folder = backup_dir(state_dir)
    if folder.is_dir():
        for path in folder.iterdir():
            match = _NAME_RE.match(path.name)
            if match and path.is_file():
                stamp = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S")
                found.append(BackupFile(path, int(stamp.timestamp()), path.stat().st_size))
    return sorted(found, key=lambda item: item.ts, reverse=True)


def parse_extra_paths(raw: str) -> list[Path]:
    return [Path(part.strip()).expanduser() for part in raw.split(",") if part.strip()]


def _add_extra(tar: tarfile.TarFile, paths: list[Path]) -> int:
    total = 0
    for root in paths:
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []
        for file in files:
            try:
                size = file.stat().st_size
                if file.is_symlink() or size > MAX_EXTRA_FILE or total + size > MAX_EXTRA_TOTAL:
                    continue
                tar.add(file, arcname=f"extra/{str(file.resolve()).lstrip('/')}", recursive=False)
                total += size
            except OSError as exc:
                log.warning("Backup: skipping %s: %s", file, exc)
    return total


async def create_backup(
    state_dir: Path, db: Database, *, keep: int = 7, extra: list[Path] | None = None, tz: tzinfo | None = None
) -> BackupFile:
    folder = backup_dir(state_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz).strftime("%Y%m%d-%H%M%S")
    target = folder / f"homebot-{stamp}.tar.gz"
    tmp_target = target.with_suffix(".tmp")
    try:
        with tempfile.TemporaryDirectory(dir=folder) as scratch:
            db_copy = Path(scratch) / DB_NAME
            await db.backup_to(db_copy)
            with tarfile.open(tmp_target, "w:gz") as tar:
                tar.add(db_copy, arcname=DB_NAME, recursive=False)
                state_file = state_dir / STATE_NAME
                if state_file.is_file():
                    tar.add(state_file, arcname=STATE_NAME, recursive=False)
                _add_extra(tar, extra or [])
        tmp_target.replace(target)
    except (OSError, tarfile.TarError, sqlite3.Error, RuntimeError) as exc:
        tmp_target.unlink(missing_ok=True)
        raise BackupError(f"не получилось сохранить: {exc}") from exc
    prune(state_dir, keep)
    return BackupFile(target, int(time.time()), target.stat().st_size)


def prune(state_dir: Path, keep: int) -> int:
    removed = 0
    for old in list_backups(state_dir)[max(1, keep) :]:
        old.path.unlink(missing_ok=True)
        removed += 1
    return removed


def _safe_member(tar: tarfile.TarFile, name: str) -> tarfile.TarInfo:
    try:
        member = tar.getmember(name)
    except KeyError as exc:
        raise BackupError(f"в архиве нет {name}") from exc
    if not member.isreg() or member.size > 500 * 1024 * 1024:
        raise BackupError(f"{name}: неожиданный тип или размер")
    return member


async def restore_backup(path: Path, state_dir: Path, db: Database, store: StateStore) -> list[str]:
    """Возвращает, что восстановлено. Сначала всё проверяется, потом заменяется."""
    done: list[str] = []
    with tempfile.TemporaryDirectory(dir=backup_dir(state_dir)) as scratch:
        try:
            with tarfile.open(path, "r:gz") as tar:
                db_member = _safe_member(tar, DB_NAME)
                handle = tar.extractfile(db_member)
                if handle is None:
                    raise BackupError("не прочиталась база")
                db_file = Path(scratch) / DB_NAME
                with handle, db_file.open("wb") as out:
                    shutil.copyfileobj(handle, out)
                state_data: dict[str, Any] | None = None
                if STATE_NAME in tar.getnames():
                    state_handle = tar.extractfile(_safe_member(tar, STATE_NAME))
                    parsed = json.loads(state_handle.read().decode("utf-8")) if state_handle else None
                    if not isinstance(parsed, dict):
                        raise BackupError("state.json в копии повреждён")
                    state_data = parsed
        except (tarfile.TarError, OSError, ValueError, EOFError) as exc:
            raise BackupError(f"архив не открывается: {exc}") from exc
        try:
            await db.restore_from(db_file)
        except Exception as exc:
            raise BackupError(f"база не восстановилась: {exc}") from exc
        done.append("база")
        if state_data is not None:
            await store.replace_all(state_data)
            done.append("состояние")
    return done


async def run_backup(
    state_dir: Path, db: Database, store: StateStore, *, keep: int, extra: list[Path], tz: tzinfo | None
) -> BackupFile:
    """Делает бэкап и запоминает результат (для /doctor и для «пора ли делать»)."""
    try:
        item = await create_backup(state_dir, db, keep=keep, extra=extra, tz=tz)
    except BackupError as exc:
        await store.set(STATE_KEY, {"ts": int(time.time()), "ok": False, "error": str(exc)})
        raise
    await store.set(STATE_KEY, {"ts": item.ts, "ok": True, "name": item.name, "size": item.size})
    return item


def last_status(store: StateStore) -> dict[str, Any]:
    data = store.get(STATE_KEY)
    return data if isinstance(data, dict) else {}
