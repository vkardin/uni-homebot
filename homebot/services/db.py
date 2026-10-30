"""SQLite-хранилище: история метрик, напоминания, заметки.

Одно соединение на весь процесс, доступ из потоков под `threading.Lock`; все операции
выполняются через `asyncio.to_thread`, поэтому event loop не блокируется.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    ts   INTEGER PRIMARY KEY,
    cpu  REAL NOT NULL,
    ram  REAL NOT NULL,
    swap REAL NOT NULL,
    temp REAL,
    disk REAL
);
CREATE TABLE IF NOT EXISTS reminders (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    due_ts INTEGER NOT NULL,
    text   TEXT NOT NULL,
    done   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders (done, due_ts);
CREATE TABLE IF NOT EXISTS notes (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS news_seen (
    key          TEXT PRIMARY KEY,
    feed         TEXT NOT NULL,
    title        TEXT NOT NULL,
    link         TEXT,
    published_ts INTEGER,
    seen_ts      INTEGER NOT NULL,
    sent_ts      INTEGER,
    retold_title TEXT,
    retold_text  TEXT
);
CREATE INDEX IF NOT EXISTS news_seen_feed ON news_seen (feed);
CREATE INDEX IF NOT EXISTS news_seen_sent ON news_seen (sent_ts);
"""


@dataclass(frozen=True, slots=True)
class MetricRow:
    ts: int
    cpu: float
    ram: float
    swap: float
    temp: float | None
    disk: float | None


@dataclass(frozen=True, slots=True)
class Reminder:
    id: int
    due_ts: int
    text: str
    repeat: str = ""
    owner: int = 0  # 0 — администратор, иначе Telegram ID гостя


@dataclass(frozen=True, slots=True)
class SeenNews:
    key: str
    feed: str
    title: str
    link: str | None
    published_ts: int | None


@dataclass(frozen=True, slots=True)
class SentNews:
    title: str
    text: str
    link: str | None
    feed: str
    sent_ts: int


@dataclass(frozen=True, slots=True)
class Note:
    id: int
    ts: int
    text: str


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(reminders)")}
        if "repeat" not in columns:
            conn.execute("ALTER TABLE reminders ADD COLUMN repeat TEXT NOT NULL DEFAULT ''")
        if "owner" not in columns:
            conn.execute("ALTER TABLE reminders ADD COLUMN owner INTEGER NOT NULL DEFAULT 0")
        if "owner" not in {row[1] for row in conn.execute("PRAGMA table_info(notes)")}:
            conn.execute("ALTER TABLE notes ADD COLUMN owner INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        self._conn = conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ----------------------------------------------------------- внутреннее
    def _call(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        with self._lock:
            if self._conn is None:
                raise RuntimeError("database is not open")
            try:
                result = fn(self._conn)
                self._conn.commit()
                return result
            except sqlite3.Error:
                self._conn.rollback()
                raise

    async def _run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        return await asyncio.to_thread(self._call, fn)

    # --------------------------------------------------------------- бэкап
    async def backup_to(self, dest: Path) -> None:
        """Согласованная копия базы (sqlite backup API) — можно делать на ходу, пока бот работает."""

        def copy(c: sqlite3.Connection) -> None:
            target = sqlite3.connect(dest)
            try:
                c.backup(target)
            finally:
                target.close()

        await self._run(copy)

    async def restore_from(self, src: Path) -> None:
        """Заменяет содержимое базы копией из файла. Файл сначала проверяется."""

        def check_and_copy(c: sqlite3.Connection) -> None:
            source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            try:
                if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("копия базы повреждена")
                source.backup(c)
            finally:
                source.close()

        await self._run(check_and_copy)

    # -------------------------------------------------------------- метрики
    async def add_metric(self, row: MetricRow) -> None:
        await self._run(
            lambda c: c.execute(
                "INSERT OR REPLACE INTO metrics (ts, cpu, ram, swap, temp, disk) VALUES (?, ?, ?, ?, ?, ?)",
                (row.ts, row.cpu, row.ram, row.swap, row.temp, row.disk),
            )
        )

    async def metrics_since(self, since_ts: int) -> list[MetricRow]:
        def query(c: sqlite3.Connection) -> list[MetricRow]:
            rows = c.execute("SELECT ts, cpu, ram, swap, temp, disk FROM metrics WHERE ts >= ? ORDER BY ts", (since_ts,)).fetchall()
            return [MetricRow(*row) for row in rows]

        return await self._run(query)

    async def prune_metrics(self, before_ts: int) -> int:
        return await self._run(lambda c: c.execute("DELETE FROM metrics WHERE ts < ?", (before_ts,)).rowcount)

    # ---------------------------------------------------------- напоминания
    async def add_reminder(self, due_ts: int, text: str, repeat: str = "", owner: int = 0) -> int:
        def insert(c: sqlite3.Connection) -> int:
            cursor = c.execute(
                "INSERT INTO reminders (due_ts, text, repeat, owner) VALUES (?, ?, ?, ?)", (due_ts, text, repeat, owner)
            )
            return int(cursor.lastrowid or 0)

        return await self._run(insert)

    async def due_reminders(self, now_ts: int) -> list[Reminder]:
        def query(c: sqlite3.Connection) -> list[Reminder]:
            rows = c.execute(
                "SELECT id, due_ts, text, repeat, owner FROM reminders WHERE done = 0 AND due_ts <= ? ORDER BY due_ts", (now_ts,)
            ).fetchall()
            return [Reminder(*row) for row in rows]

        return await self._run(query)

    async def pending_reminders(self, limit: int = 20, owner: int = 0) -> list[Reminder]:
        def query(c: sqlite3.Connection) -> list[Reminder]:
            rows = c.execute(
                "SELECT id, due_ts, text, repeat, owner FROM reminders WHERE done = 0 AND owner = ? ORDER BY due_ts LIMIT ?",
                (owner, limit),
            ).fetchall()
            return [Reminder(*row) for row in rows]

        return await self._run(query)

    async def mark_reminder_done(self, reminder_id: int) -> None:
        await self._run(lambda c: c.execute("UPDATE reminders SET done = 1 WHERE id = ?", (reminder_id,)))

    async def reschedule_reminder(self, reminder_id: int, due_ts: int) -> None:
        await self._run(lambda c: c.execute("UPDATE reminders SET due_ts = ? WHERE id = ?", (due_ts, reminder_id)))

    async def delete_reminder(self, reminder_id: int, owner: int = 0) -> bool:
        return await self._run(
            lambda c: c.execute("DELETE FROM reminders WHERE id = ? AND owner = ?", (reminder_id, owner)).rowcount > 0
        )

    async def count_reminders(self, owner: int) -> int:
        return await self._run(
            lambda c: c.execute("SELECT COUNT(*) FROM reminders WHERE done = 0 AND owner = ?", (owner,)).fetchone()[0]
        )

    # -------------------------------------------------------------- заметки
    async def add_note(self, ts: int, text: str, owner: int = 0) -> int:
        def insert(c: sqlite3.Connection) -> int:
            cursor = c.execute("INSERT INTO notes (ts, text, owner) VALUES (?, ?, ?)", (ts, text, owner))
            return int(cursor.lastrowid or 0)

        return await self._run(insert)

    async def list_notes(self, limit: int = 15, owner: int = 0) -> list[Note]:
        def query(c: sqlite3.Connection) -> list[Note]:
            rows = c.execute(
                "SELECT id, ts, text FROM notes WHERE owner = ? ORDER BY ts DESC, id DESC LIMIT ?", (owner, limit)
            ).fetchall()
            return [Note(*row) for row in rows]

        return await self._run(query)

    async def delete_note(self, note_id: int, owner: int = 0) -> bool:
        return await self._run(lambda c: c.execute("DELETE FROM notes WHERE id = ? AND owner = ?", (note_id, owner)).rowcount > 0)

    async def count_notes(self, owner: int) -> int:
        return await self._run(lambda c: c.execute("SELECT COUNT(*) FROM notes WHERE owner = ?", (owner,)).fetchone()[0])

    # -------------------------------------------------------------- новости
    async def known_news(self, keys: list[str]) -> set[str]:
        if not keys:
            return set()

        def query(c: sqlite3.Connection) -> set[str]:
            found: set[str] = set()
            for start in range(0, len(keys), 500):  # лимит параметров SQLite
                chunk = keys[start : start + 500]
                marks = ",".join("?" * len(chunk))
                found.update(row[0] for row in c.execute(f"SELECT key FROM news_seen WHERE key IN ({marks})", chunk))
            return found

        return await self._run(query)

    async def feeds_with_history(self, feeds: list[str]) -> set[str]:
        """Ленты, которые бот уже читал: для них новость без даты = действительно новая."""
        if not feeds:
            return set()

        def query(c: sqlite3.Connection) -> set[str]:
            marks = ",".join("?" * len(feeds))
            return {row[0] for row in c.execute(f"SELECT DISTINCT feed FROM news_seen WHERE feed IN ({marks})", feeds)}

        return await self._run(query)

    async def mark_news_seen(self, items: list[SeenNews], seen_ts: int) -> None:
        if not items:
            return
        rows = [(i.key, i.feed, i.title, i.link, i.published_ts, seen_ts) for i in items]
        await self._run(
            lambda c: c.executemany(
                "INSERT OR IGNORE INTO news_seen (key, feed, title, link, published_ts, seen_ts) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        )

    async def mark_news_sent(self, entries: list[tuple[str, str, str]], sent_ts: int) -> None:
        """entries: (key, пересказанный заголовок, пересказ)."""
        if not entries:
            return
        rows = [(sent_ts, title, text, key) for key, title, text in entries]
        await self._run(
            lambda c: c.executemany("UPDATE news_seen SET sent_ts = ?, retold_title = ?, retold_text = ? WHERE key = ?", rows)
        )

    async def recent_sent_news(self, limit: int = 10) -> list[SentNews]:
        def query(c: sqlite3.Connection) -> list[SentNews]:
            rows = c.execute(
                "SELECT COALESCE(retold_title, title), COALESCE(retold_text, ''), link, feed, sent_ts "
                "FROM news_seen WHERE sent_ts IS NOT NULL ORDER BY sent_ts DESC, rowid LIMIT ?",
                (limit,),
            ).fetchall()
            return [SentNews(*row) for row in rows]

        return await self._run(query)

    async def sent_news_since(self, since_ts: int, limit: int = 30) -> list[SentNews]:
        """Отправленные новости начиная с момента (новые сверху) — для блока «Ранее сегодня»."""

        def query(c: sqlite3.Connection) -> list[SentNews]:
            rows = c.execute(
                "SELECT COALESCE(retold_title, title), COALESCE(retold_text, ''), link, feed, sent_ts "
                "FROM news_seen WHERE sent_ts >= ? ORDER BY sent_ts DESC, rowid LIMIT ?",
                (since_ts, limit),
            ).fetchall()
            return [SentNews(*row) for row in rows]

        return await self._run(query)

    async def prune_news(self, before_ts: int) -> int:
        return await self._run(lambda c: c.execute("DELETE FROM news_seen WHERE seen_ts < ?", (before_ts,)).rowcount)
