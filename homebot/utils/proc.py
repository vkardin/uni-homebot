"""Запуск внешних команд: никогда не бросает исключений, всегда с таймаутом."""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    error: str | None = None  # команда не найдена, таймаут, ошибка запуска

    @property
    def ok(self) -> bool:
        return self.error is None and self.returncode == 0

    @property
    def problem(self) -> str:
        """Короткое описание сбоя для показа пользователю."""
        if self.error:
            return self.error
        text = " ".join(self.stderr.split()) or " ".join(self.stdout.split())
        return (text or f"код возврата {self.returncode}")[:200]


async def run_command(*args: str, timeout: float = 10.0) -> CommandResult:  # noqa: ASYNC109 - явный таймаут процесса
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}  # предсказуемый вывод для разбора
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        return CommandResult(127, error=f"{args[0]}: команда не найдена")
    except OSError as exc:
        return CommandResult(126, error=f"{args[0]}: {str(exc)[:100]}")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        with suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return CommandResult(124, error=f"{args[0]}: таймаут {timeout:.0f} с")

    return CommandResult(
        proc.returncode if proc.returncode is not None else 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )
