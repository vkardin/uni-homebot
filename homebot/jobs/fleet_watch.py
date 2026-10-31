"""🛰 Присмотр за другими машинами: недоступен агент или заполнен диск — сообщение; свежий бэкап — копия на агент."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from homebot.services import backup, fleet
from homebot.ui.update_texts import esc

log = logging.getLogger(__name__)
KEY_PUSHED = "fleet.pushed"  # {агент: имя последнего отправленного бэкапа}
FAILS_BEFORE_ALERT = 3


class FleetWatchJob:
    def __init__(self, *, bot: Any, admin_id: int, store: Any, http: Any, state_dir: Path, disk_limit: float,
                 interval: int = 300) -> None:  # fmt: skip
        self._bot, self._admin, self._store, self._http = bot, admin_id, store, http
        self._dir, self._disk, self._interval = state_dir, disk_limit, interval
        self._fails: dict[str, int] = {}
        self._disk_alerted: set[str] = set()

    async def _say(self, text: str) -> None:
        try:
            await self._bot.send_message(self._admin, text)
        except Exception as exc:  # Telegram недоступен — попробуем в следующий раз
            log.info("Fleet alert not sent: %s", exc)

    async def tick(self) -> None:
        agents = fleet.load(self._dir)
        for agent in agents:
            try:
                m = await fleet.metrics(self._http, agent)
            except Exception:
                n = self._fails.get(agent.name, 0) + 1
                self._fails[agent.name] = n
                if n == FAILS_BEFORE_ALERT:
                    await self._say(f"🛰 <b>{esc(agent.name)}</b> не отвечает уже {n} проверки подряд.")
                continue
            if self._fails.pop(agent.name, 0) >= FAILS_BEFORE_ALERT:
                await self._say(f"🛰 <b>{esc(agent.name)}</b> снова на связи.")
            disk = m.get("disk")
            if isinstance(disk, int | float) and disk >= self._disk and agent.name not in self._disk_alerted:
                self._disk_alerted.add(agent.name)
                await self._say(f"🛰 <b>{esc(agent.name)}</b>: диск заполнен на {disk:.0f}%.")
            elif isinstance(disk, int | float) and disk < self._disk - 5:
                self._disk_alerted.discard(agent.name)
        await self._push_backups(agents)

    async def _push_backups(self, agents: list[fleet.Agent]) -> None:
        items = backup.list_backups(self._dir)
        if not items:
            return
        latest = items[0]
        pushed = dict(self._store.get(KEY_PUSHED) or {})
        for agent in (a for a in agents if a.backups):
            if pushed.get(agent.name) == latest.name:
                continue
            try:
                await fleet.push_backup(self._http, agent, latest.path)
            except Exception as exc:
                log.info("Backup copy to %s failed: %s", agent.name, exc)
                continue
            pushed[agent.name] = latest.name
            await self._store.set(KEY_PUSHED, pushed)

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("Fleet watch failed")
            await asyncio.sleep(self._interval)
