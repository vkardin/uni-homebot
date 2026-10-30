"""Слежение за SSH в реальном времени: каждый вход — сразу, неудачные попытки — сводкой."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from homebot.services.quiet import Importance, set_importance
from homebot.services.ssh_log import JOURNAL_BASE, FailBuffer, parse_ssh_line
from homebot.ui.texts import render_ssh_failures, render_ssh_login

log = logging.getLogger(__name__)


class SshWatchJob:
    def __init__(self, *, bot: Bot, admin_id: int, report_every: int) -> None:
        self._bot = bot
        self._admin_id = admin_id
        self._report_every = report_every
        self._failures = FailBuffer()

    async def run(self) -> None:
        set_importance(Importance.CRITICAL)  # 🌙 ночной режим: см. services/quiet.py
        log.info("SSH watch running")
        backoff = 5.0
        while True:
            started = time.monotonic()
            try:
                await self._follow()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("SSH watch failed")
            if time.monotonic() - started > 60:
                backoff = 5.0  # проработал нормально — значит, сбой разовый
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300.0)

    async def _follow(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            *JOURNAL_BASE,
            "-f",
            "-n",
            "0",
            "-o",
            "cat",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        failures = self._failures
        next_flush = time.monotonic() + self._report_every
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), max(0.5, next_flush - time.monotonic()))
                except TimeoutError:
                    raw = None
                if raw == b"":
                    log.warning("journalctl exited with code %s; restarting", await proc.wait())
                    return
                if raw:
                    event = parse_ssh_line(raw.decode(errors="replace"))
                    if event is not None and event.kind == "login":
                        await self._send(render_ssh_login(event))
                    elif event is not None:
                        failures.add(event)
                if time.monotonic() >= next_flush:
                    next_flush = time.monotonic() + self._report_every
                    if summary := failures.take():
                        await self._send(render_ssh_failures(*summary, minutes=self._report_every // 60 or 1))
        finally:
            if proc.returncode is None:
                with suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()

    async def _send(self, text: str) -> None:
        try:
            await self._bot.send_message(self._admin_id, text)
        except TelegramAPIError as exc:
            log.warning("Cannot deliver SSH notification: %s", exc)
