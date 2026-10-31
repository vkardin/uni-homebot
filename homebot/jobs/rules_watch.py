"""⚡ Сценарии: раз в минуту собирает факты (нагрузка, службы, контейнеры, курс) и выполняет сработавшие правила."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homebot.services import rules
from homebot.ui.update_texts import esc

log = logging.getLogger(__name__)


class RulesJob:
    def __init__(self, *, bot: Any, admin_id: int, store: Any, system: Any, units: Any, apps: Any, power: Any,
                 interval: int = 60) -> None:  # fmt: skip
        self._bot, self._admin, self._store = bot, admin_id, store
        self._system, self._units, self._apps, self._power = system, units, apps, power
        self._interval = interval
        self.engine = rules.Engine()

    async def facts(self, items: list[rules.Rule]) -> dict[str, Any]:
        need = {r.when for r in items if r.on}
        out: dict[str, Any] = {}
        if need & {"cpu", "ram", "disk", "temp"}:
            snap = await self._system.snapshot()
            out["cpu"] = getattr(snap, "cpu_percent", None)
            out["ram"] = getattr(snap, "ram_percent", None)
            disks = getattr(snap, "disks", None) or []
            out["disk"] = max((d.percent for d in disks), default=None)
            temp = getattr(snap, "temperature", None)
            out["temp"] = getattr(temp, "current", None) if temp is not None else None
        if "service_failed" in need:
            out["failed"] = {u.name for u in await self._units.status_all() if getattr(u, "active_state", "") == "failed"}
        if "container_down" in need and self._apps.docker is not None:
            raw = await self._apps.docker.containers_raw()
            out["containers"] = {
                str(n).lstrip("/") for c in raw if isinstance(c, dict) and c.get("State") == "running" for n in c.get("Names") or []
            }
        if need & {"p2p_above", "p2p_below"} and self._apps.p2p is not None:
            out["p2p"] = (await self._apps.p2p.quote()).best
        return out

    async def act(self, r: rules.Rule) -> str:
        if r.then == "restart_container":
            if self._apps.docker is None:
                return "Docker не подключён"
            ok, detail = await self._apps.docker.restart(r.then_target)
            return ("✅ " if ok else "❌ ") + detail
        if r.then == "restart_service":
            res = await self._power.restart(r.then_target)
            return ("✅ " if res.ok else "❌ ") + res.message
        return ""

    async def tick(self) -> list[rules.Rule]:
        items = rules.load(self._store)
        if not any(r.on for r in items):
            return []
        try:
            facts = await self.facts(items)
        except Exception as exc:  # часть данных недоступна — правила с ней просто подождут
            log.info("Rules facts failed: %s", exc)
            facts = {}
        fired = self.engine.due(items, facts)
        for r in fired:
            detail = await self.act(r)
            text = f"⚡ <b>Сценарий</b>: {esc(rules.describe(r))}" + (f"\n{esc(detail)}" if detail else "")
            try:
                await self._bot.send_message(self._admin, text)
            except Exception as exc:
                log.info("Rule message not sent: %s", exc)
        return fired

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("Rules tick failed")
            await asyncio.sleep(self._interval)
