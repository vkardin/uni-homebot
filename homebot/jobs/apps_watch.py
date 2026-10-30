"""Наблюдение за приложениями: упавшие контейнеры, плохой замер скорости, завершённые торренты.

Каждое событие отправляется один раз: состояние «о чём уже сообщили» хранится в StateStore и
переживает перезапуск бота. Если доставка не удалась, событие не считается отправленным.
"""

from __future__ import annotations

import asyncio
import html
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, tzinfo
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from homebot.services.apps_http import AppError
from homebot.services.docker_api import Container, DockerService
from homebot.services.docker_diag import DIAG_PROMPT, Asker, prepare_diag_input, render_tail, tidy_diag
from homebot.services.llm_queue import Priority, set_priority
from homebot.services.qbittorrent import QbitService
from homebot.services.quiet import Importance, set_importance
from homebot.services.speed_analysis import assess, split_weeks
from homebot.services.speedtest import SpeedtestService
from homebot.services.state import StateStore
from homebot.ui.extra_texts import render_speed_drop, render_speed_recovered, render_speed_week
from homebot.utils.formatting import fmt_bytes

log = logging.getLogger(__name__)
esc = html.escape


class _Notifier:
    def __init__(self, bot: Bot, admin_id: int) -> None:
        self._bot = bot
        self._admin_id = admin_id

    async def send(self, text: str) -> bool:
        try:
            await self._bot.send_message(self._admin_id, text)
        except TelegramAPIError as exc:
            log.warning("Cannot deliver app notification: %s", exc)
            return False
        return True

    async def send_message(self, text: str, reply_markup: Any = None) -> int | None:
        """Как `send`, но возвращает номер сообщения (чтобы потом его дополнить); None — не доставлено."""
        try:
            if reply_markup is None:
                sent = await self._bot.send_message(self._admin_id, text)
            else:
                sent = await self._bot.send_message(self._admin_id, text, reply_markup=reply_markup)
        except TelegramAPIError as exc:
            log.warning("Cannot deliver app notification: %s", exc)
            return None
        message_id = getattr(sent, "message_id", None)
        return message_id if isinstance(message_id, int) else 0

    async def edit(self, message_id: int, text: str, reply_markup: Any = None) -> None:
        try:
            if reply_markup is None:
                await self._bot.edit_message_text(text=text, chat_id=self._admin_id, message_id=message_id)
            else:
                await self._bot.edit_message_text(text=text, chat_id=self._admin_id, message_id=message_id, reply_markup=reply_markup)
        except TelegramAPIError as exc:
            log.debug("Cannot edit app notification: %s", exc)


async def _loop(name: str, interval: float, tick: Callable[[], Awaitable[None]]) -> None:
    log.info("%s watch running (every %s s)", name, interval)
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except AppError as exc:
            log.warning("%s watch: %s", name, exc)
        except Exception:
            log.exception("%s watch failed", name)
        await asyncio.sleep(interval)


class DockerWatchJob:
    """Сообщает о проблемах контейнеров: с хвостом журнала, кнопками и (в фоне) разбором от модели.

    Автолечение — только для контейнеров из `autoheal` и не чаще `HEAL_LIMIT` раз в час на контейнер.
    """

    KEY = "docker.problems"
    MAX_DETAILED = 3  # у скольких контейнеров в одном сообщении показываем журнал
    HEAL_LIMIT = 3
    DIAG_TIMEOUT = 300

    def __init__(
        self,
        *,
        bot: Bot,
        admin_id: int,
        docker: DockerService,
        store: StateStore,
        ignore: set[str],
        interval: int = 60,
        asker: Asker | None = None,
        autoheal: frozenset[str] | set[str] = frozenset(),
        markup: Callable[[Sequence[str], bool], Any] | None = None,
    ) -> None:
        self._notify = _Notifier(bot, admin_id)
        self._docker = docker
        self._store = store
        self._ignore = ignore
        self._interval = interval
        self._asker = asker
        self._autoheal = frozenset(autoheal)
        self._markup = markup
        self._heals: dict[str, list[float]] = {}
        self._background: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        set_importance(Importance.CRITICAL)  # 🌙 ночной режим: см. services/quiet.py
        set_priority(Priority.ALERT)  # очередь к модели: см. services/llm_queue.py
        try:
            await _loop("Docker", self._interval, self.tick)
        finally:
            for task in list(self._background):
                task.cancel()

    # ---- автолечение
    def _can_heal(self, name: str) -> bool:
        if name not in self._autoheal or not self._docker.restart_enabled:
            return False
        recent = [t for t in self._heals.get(name, []) if time.monotonic() - t < 3600]
        self._heals[name] = recent
        return len(recent) < self.HEAL_LIMIT

    async def _heal(self, name: str) -> str:
        self._heals.setdefault(name, []).append(time.monotonic())
        ok, message = await self._docker.restart(name)
        return "🔧 перезапустил автоматически" if ok else f"🔧 автоперезапуск не вышел: {esc(message)}"

    async def _logs(self, name: str, tail: int) -> str:
        try:
            return await self._docker.logs(name, tail=tail)
        except AppError as exc:
            log.debug("Docker watch: no logs for %s: %s", name, exc)
            return ""

    async def tick(self) -> None:
        containers = {c.name: c for c in await self._docker.containers() if c.problem and c.name not in self._ignore}
        current = {name: c.problem for name, c in containers.items()}
        known: dict[str, str] = dict(self._store.get(self.KEY) or {})
        new = {name: problem for name, problem in current.items() if name not in known}
        recovered = [name for name in known if name not in current]
        if not new and not recovered:
            return

        lines = ["🐳 <b>Docker</b>"]
        detailed: list[str] = []
        for name, problem in sorted(new.items()):
            lines.append(f"🔴 <code>{esc(name)}</code>: {esc(problem or '')}")
            if self._can_heal(name):
                lines.append(await self._heal(name))
            elif name in self._autoheal and self._docker.restart_enabled:
                lines.append(f"🔧 автоперезапуск исчерпан ({self.HEAL_LIMIT} за час) — смотрите журнал")
            if len(detailed) < self.MAX_DETAILED:
                tail = render_tail(await self._logs(name, 12))
                if tail:
                    lines.append(tail)
                detailed.append(name)
        lines += [f"🟢 <code>{esc(name)}</code>: снова в порядке" for name in sorted(recovered)]

        text = "\n".join(lines)
        buttons = [name for name in sorted(new) if len(name) <= 30]
        markup = self._markup(buttons, self._docker.restart_enabled) if (self._markup and buttons) else None
        message_id = await self._notify.send_message(text, markup)
        if message_id is None:
            return
        await self._store.set(self.KEY, dict(current))
        if self._asker is not None and message_id and detailed:
            task = asyncio.create_task(self._diagnose(message_id, text, markup, detailed, containers))
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    async def _diagnose(self, message_id: int, base: str, markup: Any, names: Sequence[str], containers: dict[str, Container]) -> None:
        """Разбор журнала моделью: сообщение уже ушло, дополняем его, когда ответ готов (на CPU это минуты)."""
        assert self._asker is not None
        parts: list[str] = []
        for name in names:
            logs = await self._logs(name, 60)
            if not logs.strip():
                continue
            problem = containers[name].problem or ""
            question = prepare_diag_input(name, problem, logs)
            try:
                answer = await asyncio.wait_for(self._asker.ask(DIAG_PROMPT, question), self.DIAG_TIMEOUT)
            except Exception as exc:  # модель занята/недоступна — сообщение и так информативно
                log.info("Docker diagnosis for %s skipped: %s", name, exc)
                continue
            if tidy := tidy_diag(answer):
                parts.append(f"🧠 <b>{esc(name)}</b>: {esc(tidy)}")
        if parts:
            await self._notify.edit(message_id, base + "\n\n" + "\n".join(parts), markup)


class SpeedtestWatchJob:
    """Следит за замерами: сбой, абсолютный порог, «упала относительно обычной» и воскресный отчёт."""

    KEY = "speedtest.last_id"
    KEY_DEGRADED = "speedtest.degraded"
    KEY_REPORT = "speedtest.report_week"
    KEY_FAILS = "speedtest.fails"  # неудачных замеров подряд
    FAIL_ALERT_AFTER = 2  # один сбой Ookla — обычное дело; тревога — со второго подряд
    IGNORED = ("started", "queued", "running", "checking", "benchmarking", "waiting", "skipped")
    REPORT_WEEKDAY = 6  # воскресенье
    REPORT_HOUR = 20

    def __init__(
        self,
        *,
        bot: Bot,
        admin_id: int,
        speedtest: SpeedtestService,
        store: StateStore,
        min_down: float,
        interval: int = 600,
        drop_percent: int = 0,
        tz: tzinfo | None = None,
        weekly: bool = False,
        chart: Callable[[Sequence[tuple[datetime, float, float]]], Awaitable[bytes]] | None = None,
        photo: Callable[[bytes, str], Awaitable[bool]] | None = None,
        markup: Callable[[], Any] | None = None,
    ) -> None:
        self._bot = bot
        self._admin_id = admin_id
        self._notify = _Notifier(bot, admin_id)
        self._markup = markup  # кнопки под сообщением о сбое («Перемерить»)
        self._speedtest = speedtest
        self._store = store
        self._min_down = min_down
        self._interval = interval
        self._drop_percent = drop_percent
        self._tz = tz
        self._weekly_enabled = weekly and tz is not None
        self._chart = chart
        self._photo = photo

    async def run(self) -> None:
        set_importance(Importance.NORMAL)  # 🌙 ночной режим: см. services/quiet.py
        await _loop("Speedtest", self._interval, self.tick)

    async def tick(self) -> None:
        if self._weekly_enabled:
            try:
                await self.weekly_report()
            except AppError as exc:
                log.warning("Speedtest weekly report: %s", exc)
        await self._check_latest()

    async def _check_latest(self) -> None:
        result = await self._speedtest.latest()
        if result is None or result.status.lower() in self.IGNORED:
            return
        last_id = self._store.get(self.KEY)
        if last_id == result.id:
            return
        if last_id is None:  # первый запуск: запоминаем, о старом не сообщаем
            await self._store.set(self.KEY, result.id)
            return
        text = None
        markup = None
        fails = int(self._store.get(self.KEY_FAILS) or 0)
        if not result.ok:
            fails += 1
            if fails == self.FAIL_ALERT_AFTER:  # ровно на втором подряд — дальше не повторяем каждые 10 минут
                reason = f"\n<i>{esc(result.message)}</i>" if result.message else ""
                text = f"🚀 <b>Speedtest</b>: {fails} замера подряд не удались ({esc(result.status or '?')}){reason}"
                markup = self._markup() if self._markup is not None else None
        elif fails >= self.FAIL_ALERT_AFTER:
            text = "✅ <b>Speedtest</b>: замеры снова проходят"
        if result.ok:
            fails = 0
        slow = result.ok and self._min_down and result.download_mbps is not None and result.download_mbps < self._min_down
        if text is None and slow:
            text = (
                f"🐢 <b>Медленный интернет</b>: ⬇️ {result.download_mbps:.0f} Мбит/с "
                f"(порог {self._min_down:.0f}) · ⬆️ {result.upload_mbps or 0:.0f} · пинг {result.ping_ms or 0:.0f} мс"
            )
        degraded_now, relative_text = await self._relative(result.ok)
        if text is None:
            text = relative_text
        delivered = text is None or (await self._notify.send_message(text, markup)) is not None
        if delivered:
            await self._store.set(self.KEY, result.id)
            await self._store.set(self.KEY_FAILS, fails)
            if degraded_now is not None:
                await self._store.set(self.KEY_DEGRADED, degraded_now)

    async def _relative(self, ok: bool) -> tuple[bool | None, str | None]:
        """(новое состояние «просело» или None — не менять, текст сообщения или None)."""
        if not ok or self._drop_percent <= 0:
            return None, None
        try:
            history = await self._speedtest.history(14)
        except AppError as exc:
            log.debug("Speedtest history unavailable: %s", exc)
            return None, None
        verdict, detail = assess(history, drop_percent=self._drop_percent)
        was = bool(self._store.get(self.KEY_DEGRADED))
        if verdict == "degraded" and detail is not None and not was:
            return True, render_speed_drop(detail)
        if verdict == "recovered" and detail is not None and was:
            return False, render_speed_recovered(detail)
        return None, None

    async def weekly_report(self, now: datetime | None = None) -> bool:
        """Воскресный отчёт. True — отправлен. Один раз за ISO-неделю; пока нет замеров — ждём следующего тика."""
        assert self._tz is not None
        local = (now or datetime.now(self._tz)).astimezone(self._tz)
        if local.weekday() != self.REPORT_WEEKDAY or local.hour < self.REPORT_HOUR:
            return False
        week = local.strftime("%G-W%V")
        if self._store.get(self.KEY_REPORT) == week:
            return False
        history = await self._speedtest.history(14, now=local)
        this, prev = split_weeks(history, local)
        if this is None:
            return False
        caption = render_speed_week(this, prev)
        sent = False
        points = [(r.created_at, r.download_mbps or 0.0, r.upload_mbps or 0.0) for r in history if r.ok and r.created_at is not None]
        points = [p for p in points if (local - p[0]).days < 7]
        if self._chart is not None and self._photo is not None and points:
            try:
                png = await self._chart(points)
                sent = await self._photo(png, caption)
            except Exception as exc:
                log.warning("Speedtest weekly chart failed: %s", exc)
        if not sent:
            sent = await self._notify.send(caption)
        if sent:
            await self._store.set(self.KEY_REPORT, week)
        return sent


class TorrentWatchJob:
    KEY = "qbit.completed"
    MAX_REMEMBERED = 1000

    def __init__(
        self,
        *,
        bot: Bot,
        admin_id: int,
        qbit: QbitService,
        store: StateStore,
        interval: int = 60,
        quiet_categories: frozenset[str] = frozenset(),
    ) -> None:
        self._notify = _Notifier(bot, admin_id)
        self._qbit = qbit
        # Загрузки Radarr/Sonarr: о них сообщает «🎬 Кино» (когда фильм уже в медиатеке), без дублей
        self._quiet = {c.lower() for c in quiet_categories}
        self._store = store
        self._interval = interval

    async def run(self) -> None:
        set_importance(Importance.NORMAL)  # 🌙 ночной режим: см. services/quiet.py
        await _loop("qBittorrent", self._interval, self.tick)

    async def tick(self) -> None:
        torrents = await self._qbit.torrents()
        done_now = {t.hash: t for t in torrents if t.done}
        stored = self._store.get(self.KEY)
        if stored is None:  # первый запуск: всё уже скачанное — не новость
            await self._store.set(self.KEY, list(done_now)[-self.MAX_REMEMBERED :])
            return
        known = set(stored)
        fresh = [t for h, t in done_now.items() if h not in known]
        if not fresh:
            return
        silent = [t for t in fresh if t.category.lower() in self._quiet]
        fresh = [t for t in fresh if t.category.lower() not in self._quiet]
        if not fresh:
            await self._store.set(self.KEY, [*stored, *(t.hash for t in silent)][-self.MAX_REMEMBERED :])
            return
        lines = [f"✅ <b>Скачано</b>: {esc(t.name)} ({fmt_bytes(t.size)})" for t in fresh[:10]]
        if len(fresh) > 10:
            lines.append(f"…и ещё {len(fresh) - 10}")
        if await self._notify.send("\n".join(lines)):
            remembered = [*stored, *(t.hash for t in silent), *(t.hash for t in fresh)]
            await self._store.set(self.KEY, remembered[-self.MAX_REMEMBERED :])
