"""🧩 Внешние модули UNI (API модулей 1.0).

Модуль — папка `STATE_DIR/modules/<id>/` с двумя файлами:

    module.json   {"id": "hello", "name": "Привет", "version": "1.0.0", "api": 1,
                   "description": "...", "permissions": ["network"],
                   "commands": [{"command": "hello", "description": "Поздороваться"}]}
    module.py     def setup(uni): ...

В `setup` модуль получает объект `ModuleContext` и только через него общается с ботом:

    uni.command("hello", handler)      # async def handler(args: str) -> str  — ответ в чат (HTML)
    uni.every(3600, job)               # async def job() -> str | None         — текст уйдёт владельцу (нужно "notify")
    uni.http                           # aiohttp-сессия (нужно "network")
    uni.docker                         # Docker API UNI (нужно "docker")
    uni.data_dir                       # своя папка для файлов (нужно "files")
    uni.log                            # журнал модуля

Модулю не дают aiogram, state.json и настройки бота, поэтому внутренние изменения UNI его не ломают: API 1.0
меняется только с новым номером `api`. Права из манифеста показываются владельцу до включения, а модуль получает
лишь то, что в них перечислено. Это ограничение по интерфейсу, а не песочница: код модуля выполняется в процессе бота,
поэтому включайте только модули, которым доверяете.

Сломанный модуль не роняет бота: ошибка импорта или `setup` записывается в статус, ошибка команды отвечает
«модуль ошибся», после трёх ошибок подряд модуль отключается до перезапуска, а задачи идут через TaskManager.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

API_VERSION = 1
PERMISSIONS: dict[str, str] = {
    "network": "ходить в интернет и локальную сеть",
    "docker": "видеть и перезапускать контейнеры",
    "files": "хранить файлы в своей папке",
    "notify": "присылать вам сообщения сам",
}
KEY_ENABLED = "plugins.enabled"
COMMAND_TIMEOUT = 60
MAX_FAILS = 3
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_CMD_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

log = logging.getLogger("homebot.plugins")

CommandFn = Callable[[str], Awaitable[str]]
JobFn = Callable[[], Awaitable[str | None]]


class ModuleError(Exception):
    """Ошибка манифеста или нарушение API. Текст показывается владельцу."""


@dataclass
class Manifest:
    id: str
    name: str
    version: str
    description: str = ""
    permissions: tuple[str, ...] = ()
    commands: tuple[tuple[str, str], ...] = ()


@dataclass
class Plugin:
    manifest: Manifest
    path: Path
    enabled: bool = False
    loaded: bool = False
    error: str = ""
    fails: int = 0
    handlers: dict[str, CommandFn] = field(default_factory=dict)
    jobs: list[tuple[int, JobFn]] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        m = self.manifest
        return {
            "id": m.id,
            "name": m.name,
            "version": m.version,
            "description": m.description,
            "permissions": [{"id": p, "text": PERMISSIONS[p]} for p in m.permissions],
            "commands": [{"command": c, "description": d} for c, d in m.commands],
            "enabled": self.enabled,
            "loaded": self.loaded,
            "error": self.error,
        }


def parse_manifest(data: Any, folder: str) -> Manifest:
    if not isinstance(data, dict):
        raise ModuleError("module.json должен быть объектом")
    mid = str(data.get("id", ""))
    if not _ID_RE.match(mid) or mid != folder:
        raise ModuleError("id: латиница, цифры и _, совпадает с именем папки")
    if data.get("api") != API_VERSION:
        raise ModuleError(f"модуль для API {data.get('api')!r}, а UNI поддерживает {API_VERSION}")
    perms = data.get("permissions", [])
    if not isinstance(perms, list) or any(p not in PERMISSIONS for p in perms):
        raise ModuleError("permissions: только " + ", ".join(PERMISSIONS))
    commands: list[tuple[str, str]] = []
    for c in data.get("commands", []) or []:
        name = str(c.get("command", "")) if isinstance(c, dict) else ""
        if not _CMD_RE.match(name):
            raise ModuleError(f"неверная команда {name!r}")
        commands.append((name, str(c.get("description", ""))[:80]))
    return Manifest(
        id=mid,
        name=str(data.get("name") or mid)[:40],
        version=str(data.get("version", "0"))[:20],
        description=str(data.get("description", ""))[:300],
        permissions=tuple(dict.fromkeys(perms)),
        commands=tuple(commands),
    )


class ModuleContext:
    """То, что видит модуль. Всё, на что нет права, недоступно."""

    def __init__(self, plugin: Plugin, *, http: Any = None, docker: Any = None, data_root: Path | None = None) -> None:
        self._p = plugin
        self._http, self._docker, self._data_root = http, docker, data_root
        self.log = logging.getLogger(f"homebot.module.{plugin.manifest.id}")

    def _need(self, perm: str) -> None:
        if perm not in self._p.manifest.permissions:
            raise ModuleError(f"нет права {perm!r} в module.json")

    def command(self, name: str, handler: CommandFn) -> None:
        if name not in {c for c, _ in self._p.manifest.commands}:
            raise ModuleError(f"команда /{name} не объявлена в module.json")
        self._p.handlers[name] = handler

    def every(self, seconds: int, job: JobFn) -> None:
        self._need("notify")
        self._p.jobs.append((max(60, int(seconds)), job))

    @property
    def http(self) -> Any:
        self._need("network")
        return self._http

    @property
    def docker(self) -> Any:
        self._need("docker")
        return self._docker

    @property
    def data_dir(self) -> Path:
        self._need("files")
        root = (self._data_root or self._p.path) / "data"
        root.mkdir(parents=True, exist_ok=True)
        return root


class Registry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.plugins: dict[str, Plugin] = {}
        self.commands: dict[str, Plugin] = {}

    def discover(self, enabled: list[str] | tuple[str, ...] = ()) -> list[Plugin]:
        self.plugins.clear()
        if not self.root.is_dir():
            return []
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            try:
                data = json.loads((folder / "module.json").read_text(encoding="utf-8"))
                manifest = parse_manifest(data, folder.name)
                plugin = Plugin(manifest, folder, enabled=manifest.id in enabled)
            except (OSError, ValueError, ModuleError) as exc:
                bad = Manifest(id=folder.name, name=folder.name, version="?")
                plugin = Plugin(bad, folder, error=f"module.json: {exc}")
            self.plugins[plugin.manifest.id] = plugin
        return list(self.plugins.values())

    def load(self, plugin: Plugin, reserved: set[str], **ctx: Any) -> bool:
        """Импортирует module.py и вызывает setup. Любая ошибка остаётся в plugin.error."""
        if plugin.error or not plugin.enabled:
            return False
        try:
            clash = {c for c, _ in plugin.manifest.commands} & (reserved | set(self.commands))
            if clash:
                raise ModuleError("команды уже заняты: " + ", ".join(sorted(clash)))
            name = f"uni_module_{plugin.manifest.id}"
            spec = importlib.util.spec_from_file_location(name, plugin.path / "module.py")
            if spec is None or spec.loader is None:
                raise ModuleError("нет module.py")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
            setup = getattr(mod, "setup", None)
            if not callable(setup):
                raise ModuleError("в module.py нет функции setup(uni)")
            setup(ModuleContext(plugin, **ctx))
        except (Exception, SystemExit) as exc:  # чужой код: ловим всё, бот продолжает работать
            plugin.handlers.clear()
            plugin.jobs.clear()
            plugin.error = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("Module %s failed to load: %s", plugin.manifest.id, plugin.error)
            return False
        plugin.loaded = True
        for command in plugin.handlers:
            self.commands[command] = plugin
        log.info("Module %s %s loaded", plugin.manifest.id, plugin.manifest.version)
        return True

    async def run_command(self, command: str, args: str) -> str | None:
        plugin = self.commands.get(command)
        if plugin is None or not plugin.loaded:
            return None
        try:
            reply = await asyncio.wait_for(plugin.handlers[command](args), COMMAND_TIMEOUT)
        except (Exception, SystemExit) as exc:
            plugin.fails += 1
            log.warning("Module %s command /%s failed: %s", plugin.manifest.id, command, exc)
            if plugin.fails >= MAX_FAILS:
                plugin.loaded = False
                plugin.error = f"отключён до перезапуска после {MAX_FAILS} ошибок подряд: {exc}"[:300]
                return f"🧩 Модуль «{plugin.manifest.name}» ошибся {MAX_FAILS} раза подряд и отключён до перезапуска бота."
            return f"🧩 Модуль «{plugin.manifest.name}» ошибся: {type(exc).__name__}"
        plugin.fails = 0
        return str(reply or "")[:4000] or "✅"

    def job_factory(
        self, plugin: Plugin, seconds: int, job: JobFn, send: Callable[[str], Awaitable[Any]]
    ) -> Callable[[], Awaitable[None]]:
        async def run() -> None:
            while True:
                await asyncio.sleep(seconds)
                if not plugin.loaded:
                    return
                try:
                    text = await asyncio.wait_for(job(), COMMAND_TIMEOUT)
                except asyncio.CancelledError:
                    raise
                except (Exception, SystemExit):
                    log.exception("Module %s job failed", plugin.manifest.id)
                    continue
                if text:
                    await send(f"🧩 <b>{plugin.manifest.name}</b>\n{str(text)[:3500]}")

        return run


def enabled_ids(store: Any) -> list[str]:
    value = store.get(KEY_ENABLED, [])
    return [str(x) for x in value] if isinstance(value, list) else []


async def set_enabled(store: Any, module_id: str, on: bool) -> list[str]:
    ids = [x for x in enabled_ids(store) if x != module_id]
    if on:
        ids.append(module_id)
    await store.set(KEY_ENABLED, ids)
    return ids


REGISTRY: Registry | None = None  # один на процесс, заполняется в main
