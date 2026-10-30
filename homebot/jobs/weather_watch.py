"""🌦 Умная погода: пишет, только если скоро дождь, ожидаются заморозки, жара или сильный ветер."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, tzinfo
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from homebot.services.apps_http import AppError
from homebot.services.prefs import Prefs
from homebot.services.quiet import Importance, set_importance
from homebot.services.weather_alerts import Nowcast, Thresholds, WeatherEvent, due_events, evaluate
from homebot.ui.extra_texts import render_weather_events

log = logging.getLogger(__name__)

KEY_SENT = "weather.alerts"


class WeatherAlertsJob:
    def __init__(
        self,
        *,
        bot: Bot,
        admin_id: int,
        store: Any,
        prefs: Prefs,
        fetch: Callable[[], Awaitable[Nowcast]],
        tz: tzinfo,
        limits: Thresholds | None = None,
        interval: int = 900,
    ) -> None:
        self._bot = bot
        self._admin_id = admin_id
        self._store = store
        self._prefs = prefs
        self._fetch = fetch
        self._tz = tz
        self._limits = limits or Thresholds()
        self._interval = interval

    async def run(self) -> None:
        set_importance(Importance.NORMAL)  # 🌙 ночной режим: см. services/quiet.py
        log.info("Weather alerts running (every %s s)", self._interval)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except AppError as exc:
                log.warning("Weather alerts: %s", exc)
            except Exception:
                log.exception("Weather alerts failed")
            await asyncio.sleep(self._interval)

    async def tick(self, now: datetime | None = None) -> list[WeatherEvent]:
        """События, о которых сообщили в этот раз."""
        moment = now or datetime.now(UTC)
        if self._prefs.night().is_quiet(moment.astimezone(self._tz).hour):
            return []  # ночью не будим: тихие часы те же, что у новостей
        events = evaluate(await self._fetch(), moment, self._limits)
        last_sent: dict[str, float] = dict(self._store.get(KEY_SENT) or {})
        fresh = due_events(events, last_sent, moment.timestamp())
        if not fresh:
            return []
        try:
            await self._bot.send_message(self._admin_id, render_weather_events(fresh, self._tz))
        except TelegramAPIError as exc:
            log.warning("Cannot deliver weather alert: %s", exc)
            return []
        stamp = moment.timestamp()
        await self._store.set(KEY_SENT, {**last_sent, **{e.kind: stamp for e in fresh}})
        return fresh
