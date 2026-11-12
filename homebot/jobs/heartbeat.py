"""🛡 Сигнал «я жив» для облачного Сторожа (extras/watchdog).

Раз в WATCHDOG_INTERVAL_SEC бот отправляет на WATCHDOG_URL/beat короткую сводку: CPU, память, диск, температура,
аптайм, версия, часовой пояс. Ничего секретного: ни токенов, ни адресов приложений, ни данных пользователей.
Когда сигналы пропадают, Сторож сам пишет владельцу через своего бота. Если облако недоступно, бот работает как обычно.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

log = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=15)


def default_metrics() -> dict[str, Any]:
    from homebot.agent import metrics

    data = metrics()
    data.pop("disks", None)  # Сторожу хватает самого заполненного диска
    return data


class HeartbeatJob:
    def __init__(self, *, http: Any, url: str, token: str, timezone: str, interval: int = 60,
                 collect: Callable[[], dict[str, Any]] = default_metrics) -> None:  # fmt: skip
        self._http, self._url, self._token = http, url.rstrip("/") + "/beat", token
        self._tz, self._interval, self._collect = timezone, interval, collect
        self._failing = False

    async def tick(self) -> bool:
        payload = await asyncio.to_thread(self._collect)
        payload["tz"] = self._tz
        try:
            async with self._http.post(
                self._url, json=payload, headers={"Authorization": f"Bearer {self._token}"}, timeout=TIMEOUT
            ) as resp:
                ok = resp.status == 200
                if resp.status == 401:
                    log.warning("Watchdog rejected the token: check WATCHDOG_TOKEN and HEARTBEAT_TOKEN")
        except (aiohttp.ClientError, TimeoutError) as exc:
            ok = False
            if not self._failing:
                log.info("Watchdog unreachable: %s", exc)
        if ok and self._failing:
            log.info("Watchdog reachable again")
        self._failing = not ok
        return ok

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("Heartbeat failed")
            await asyncio.sleep(self._interval)
