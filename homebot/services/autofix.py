"""🔧 Авторемонт контейнеров: перезапуск с лимитом попыток, паузами между ними и учётом зависимостей.

Правила (модуль «Авторемонт», выключается в 🧱 Модулях или DOCKER_AUTOFIX=false):
* сам бот чинит только то, что сломалось у него на глазах (или контейнеры из DOCKER_AUTOHEAL); то, что
  уже стояло остановленным, — только по кнопке «🔧 Починить» в /doctor или в панели;
* штатную остановку (код 0 или 143 — так завершается `docker stop`) сам не трогает: скорее всего, её сделали руками;
* не больше `MAX_ATTEMPTS` попыток за `WINDOW` на контейнер, между попытками — пауза из `BACKOFF`;
* сначала поднимаются зависимости (romm-db перед romm): по меткам Docker Compose, а без них — по имени
  в том же проекте; зависимый контейнер ждёт, пока зависимость запустится;
* если попытки кончились, бот пишет один раз, с подсказкой по коду выхода (137 — память).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from homebot.services.apps_http import AppError
from homebot.services.docker_api import Container, DockerService

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF = (0, 120, 600)  # с: перед 1-й, 2-й и 3-й попыткой
WINDOW = 6 * 3600
WAIT_UP = 60  # с: сколько ждать, пока перезапущенный контейнер поднимется
POLL = 3.0
GRACEFUL = (0, 143)  # коды штатной остановки: сам не чиним

_P = "com.docker.compose.project"
_S = "com.docker.compose.service"
_D = "com.docker.compose.depends_on"


def _fmt_bytes(value: int) -> str:
    gb = value / 1024**3
    return f"{gb:.1f} ГБ" if gb >= 1 else f"{value / 1024**2:.0f} МБ"


def hint(container: Container | None, details: dict[str, Any] | None = None) -> str:
    """Что, скорее всего, случилось и что делать — по коду выхода, флагу OOM и проверке здоровья."""
    details = details or {}
    state = details.get("State") if isinstance(details.get("State"), dict) else {}
    oom = bool(state.get("OOMKilled"))
    code = container.exit_code if container else None
    if code is None and isinstance(state.get("ExitCode"), int) and container is not None and container.state != "running":
        code = state["ExitCode"]
    host = details.get("HostConfig") if isinstance(details.get("HostConfig"), dict) else {}
    limit = int(host.get("Memory") or 0)
    if oom or code == 137:
        where = (
            f"лимит контейнера {_fmt_bytes(limit)} — поднимите mem_limit в compose"
            if limit
            else ("лимита у контейнера нет, значит не хватило памяти всему серверу — закройте лишнее или добавьте swap")
        )
        head = "Docker отметил нехватку памяти (OOM)" if oom else "код 137: процесс убит, чаще всего из-за нехватки памяти"
        return f"{head}; {where}. Свободную память видно в /status"
    if container is not None and container.state == "running" and container.health == "unhealthy":
        return "проверка здоровья не проходит и после перезапуска — смотрите журнал; часто виновата зависимость (база, сеть)"
    if container is not None and container.state == "restarting":
        return "Docker сам перезапускает его по кругу — причина в журнале"
    return {
        0: "контейнер завершился штатно — возможно, его остановили вручную",
        1: "приложение завершилось с ошибкой — причина в журнале",
        125: "Docker не смог запустить контейнер — проверьте compose (порты, тома)",
        126: "команда запуска не выполняется — проверьте образ",
        127: "команда запуска не найдена — проверьте образ",
        139: "приложение упало (segfault) — попробуйте обновить образ",
        143: "остановлен сигналом SIGTERM (docker stop)",
        255: "ошибка при запуске — чаще всего конфигурация или образ; смотрите журнал",
    }.get(code if code is not None else -1, "причина в журнале контейнера")


def broken(c: Container) -> bool:
    """Есть что чинить перезапуском (по кругу Docker и так перезапускает сам)."""
    return c.problem is not None and c.state != "restarting"


def up(c: Container | None) -> bool:
    return c is not None and c.state == "running" and c.health != "unhealthy"


def dependencies(containers: Iterable[Container]) -> dict[str, list[str]]:
    """{контейнер: [контейнеры, от которых он зависит]} в пределах одного проекта Docker Compose."""
    items = list(containers)
    by_service: dict[tuple[str, str], str] = {}
    for c in items:
        if c.labels.get(_P) and c.labels.get(_S):
            by_service[(c.labels[_P], c.labels[_S])] = c.name
    out: dict[str, list[str]] = {}
    for c in items:
        project = c.labels.get(_P, "")
        deps: list[str] = []
        raw = c.labels.get(_D, "")
        if project and raw:
            for part in raw.split(","):
                service = part.split(":", 1)[0].strip()
                name = by_service.get((project, service))
                if name and name != c.name and name not in deps:
                    deps.append(name)
        elif project:  # старые compose без метки зависимостей: romm → romm-db, romm_redis в том же проекте
            for other in items:
                if other.name != c.name and other.labels.get(_P) == project and other.name.startswith((c.name + "-", c.name + "_")):
                    deps.append(other.name)
        if deps:
            out[c.name] = deps
    return out


def plan(targets: Iterable[str], containers: Iterable[Container]) -> list[str]:
    """Порядок перезапуска: сначала неработающие зависимости, потом сами контейнеры; без повторов и циклов."""
    items = {c.name: c for c in containers}
    deps = dependencies(items.values())
    order: list[str] = []
    visiting: set[str] = set()

    def visit(name: str, wanted: bool) -> None:
        if name in order or name in visiting or name not in items:
            return
        visiting.add(name)
        for dep in deps.get(name, []):
            visit(dep, wanted=False)
        visiting.discard(name)
        if wanted or not up(items[name]):
            order.append(name)

    for name in targets:
        visit(name, wanted=True)
    return order


@dataclass(slots=True)
class Result:
    name: str
    ok: bool
    detail: str
    dependency: bool = False  # подняли как зависимость другого контейнера


@dataclass(slots=True)
class _Track:
    attempts: list[float] = field(default_factory=list)
    gave_up: bool = False


class ContainerFixer:
    """Перезапуск с проверкой: ждёт, пока контейнер поднимется, и помнит попытки для лимита и пауз."""

    def __init__(
        self,
        docker: DockerService,
        *,
        auto: bool = True,
        autoheal: Iterable[str] = (),
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        wait_up: float = WAIT_UP,
    ) -> None:
        self._docker = docker
        self.auto = auto
        self._autoheal = frozenset(autoheal)
        self._clock = clock
        self._sleep = sleep
        self._wait_up = wait_up
        self._tracks: dict[str, _Track] = {}
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self._docker.restart_enabled

    # ---- политика
    def _track(self, name: str) -> _Track:
        track = self._tracks.setdefault(name, _Track())
        now = self._clock()
        track.attempts = [t for t in track.attempts if now - t < WINDOW]
        if not track.attempts:
            track.gave_up = False
        return track

    def eligible(self, c: Container) -> bool:
        """Можно ли чинить сам (без кнопки): см. правила в начале модуля."""
        if not (self.auto and self.available and broken(c)):
            return False
        if c.name in self._autoheal:
            return True
        return c.state == "running" or c.exit_code not in GRACEFUL

    def attempts(self, name: str) -> int:
        return len(self._track(name).attempts)

    def due(self, name: str) -> bool:
        track = self._track(name)
        n = len(track.attempts)
        if n >= MAX_ATTEMPTS:
            return False
        return n == 0 or self._clock() - track.attempts[-1] >= BACKOFF[n]

    def exhausted(self, name: str) -> bool:
        return len(self._track(name).attempts) >= MAX_ATTEMPTS

    def give_up_once(self, name: str) -> bool:
        """True ровно один раз за серию неудачных попыток — чтобы «не помогло» пришло одним сообщением."""
        track = self._track(name)
        if track.gave_up or len(track.attempts) < MAX_ATTEMPTS:
            return False
        track.gave_up = True
        return True

    def forget(self, name: str) -> None:
        """Контейнер снова в порядке: следующий сбой начнётся с паузы 0 (лимит в окне при этом сохраняется)."""
        track = self._tracks.get(name)
        if track is not None:
            track.gave_up = False

    # ---- действие
    async def _snapshot(self) -> dict[str, Container]:
        return {c.name: c for c in await self._docker.containers()}

    async def _wait(self, name: str) -> Container | None:
        deadline = self._clock() + self._wait_up
        current: Container | None = None
        while True:
            try:
                current = (await self._snapshot()).get(name)
            except AppError as exc:
                log.debug("Autofix: cannot read containers: %s", exc)
            if current is not None and current.state == "running" and current.health != "starting":
                return current
            if self._clock() >= deadline:
                return current
            await self._sleep(POLL)

    async def fix(self, names: Iterable[str], *, count: bool = True) -> list[Result]:
        """Перезапускает контейнеры (и их неработающие зависимости первыми) и проверяет, что они поднялись."""
        wanted = list(dict.fromkeys(names))
        if not wanted:
            return []
        if not self.available:
            return [Result(n, False, "перезапуск контейнеров отключён (DOCKER_RESTART_ENABLED=false)") for n in wanted]
        async with self._lock:
            containers = await self._snapshot()
            order = plan(wanted, containers.values())
            results: list[Result] = []
            failed: set[str] = set()
            deps = dependencies(containers.values())
            for name in order:
                blocked = [d for d in deps.get(name, []) if d in failed]
                if blocked:
                    results.append(Result(name, False, f"не запускаю: не поднялась зависимость {', '.join(blocked)}"))
                    failed.add(name)
                    continue
                if count:
                    self._track(name).attempts.append(self._clock())
                ok, message = await self._docker.restart(name)
                if not ok:
                    results.append(Result(name, False, message, dependency=name not in wanted))
                    failed.add(name)
                    continue
                after = await self._wait(name)
                if up(after):
                    detail = "работает" + (" (проверка здоровья ещё идёт)" if after and after.health == "starting" else "")
                    results.append(Result(name, True, detail, dependency=name not in wanted))
                    continue
                details: dict[str, Any] = {}
                try:
                    details = await self._docker.inspect(name)
                except AppError as exc:
                    log.debug("Autofix: inspect %s: %s", name, exc)
                problem = after.problem if after and after.problem else "не поднялся"
                results.append(Result(name, False, f"{problem}. {hint(after, details)}", dependency=name not in wanted))
                failed.add(name)
            return results

    async def explain(self, c: Container) -> str:
        details: dict[str, Any] = {}
        try:
            details = await self._docker.inspect(c.name)
        except AppError as exc:
            log.debug("Autofix: inspect %s: %s", c.name, exc)
        return hint(c, details)
