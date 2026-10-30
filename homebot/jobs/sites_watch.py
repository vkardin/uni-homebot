"""🔎 Спрашивает у changedetection.io, что изменилось на отслеживаемых страницах, и сообщает: цена было→стало, что поменялось."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from homebot.services.apps_http import AppError
from homebot.services.quiet import Importance, set_importance
from homebot.services.sites import KEY_TARGETS, STATE_KEY, Site, SitesService, check_target, describe_change
from homebot.services.state import StateStore
from homebot.ui.sites_texts import render_site_change, render_site_error

log = logging.getLogger(__name__)

ERROR_AFTER = 6  # столько опросов подряд changedetection недоступен — сообщаем
MAX_PER_TICK = 8  # не больше стольких уведомлений за раз (остальные — на следующем опросе)


class SitesWatchJob:
    def __init__(
        self,
        *,
        bot: Bot,
        admin_id: int,
        service: SitesService,
        store: StateStore,
        interval: int = 300,
        currency: str = "₴",
        markup: Callable[[Site], Any] | None = None,
    ) -> None:
        self._bot = bot
        self._admin_id = admin_id
        self._service = service
        self._store = store
        self._interval = interval
        self._currency = currency
        self._markup = markup
        self._failures = 0
        self._error_ticks: dict[str, str] = {}  # uuid → текст ошибки, замеченной в прошлый опрос

    async def run(self) -> None:
        set_importance(Importance.NORMAL)  # 🌙 ночью без звука: цены не срочные
        log.info("Sites watch running (every %s s)", self._interval)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Sites check failed")
            await asyncio.sleep(self._interval)

    async def tick(self) -> int:
        """Один опрос. Возвращает, сколько уведомлений отправлено."""
        try:
            sites = await self._service.brief()
        except AppError as exc:
            self._failures += 1
            log.warning("Sites: %s", exc)
            if self._failures == ERROR_AFTER:
                await self._send(f"⚠️ changedetection не отвечает уже {ERROR_AFTER} опросов подряд: {exc}", None)
            return 0
        if self._failures >= ERROR_AFTER:
            await self._send("✅ changedetection снова отвечает", None)
        self._failures = 0

        saved = self._store.get(STATE_KEY)
        state = dict(saved) if isinstance(saved, dict) else {}
        first_run = "seen" not in state
        seen = {k: v for k, v in (state.get("seen") or {}).items() if isinstance(v, int)}
        errors = {k: v for k, v in (state.get("errors") or {}).items() if isinstance(v, str)}
        present = {s.uuid for s in sites}
        seen = {k: v for k, v in seen.items() if k in present}
        errors = {k: v for k, v in errors.items() if k in present}

        sent = 0
        for site in sites:
            if first_run or site.uuid not in seen:
                seen[site.uuid] = site.last_changed  # новая слежка или первый запуск: просто запоминаем «как есть»
            elif site.last_changed > seen[site.uuid] and sent < MAX_PER_TICK:
                if await self._announce(site):
                    seen[site.uuid] = site.last_changed
                    sent += 1
            elif site.last_changed < seen[site.uuid]:
                seen[site.uuid] = site.last_changed  # историю очистили — начинаем заново

            if site.error and not site.paused:
                if errors.get(site.uuid) != site.error and self._error_ticks.get(site.uuid) == site.error:
                    if await self._send(render_site_error(site), site):
                        errors[site.uuid] = site.error
                self._error_ticks[site.uuid] = site.error
            else:
                self._error_ticks.pop(site.uuid, None)
                errors.pop(site.uuid, None)
        self._error_ticks = {k: v for k, v in self._error_ticks.items() if k in present}

        await self._store.set(STATE_KEY, {"seen": seen, "errors": errors})
        return sent

    async def _announce(self, site: Site) -> bool:
        try:
            pair = await self._service.last_two(site.uuid)
        except AppError as exc:
            log.warning("Sites: cannot read history of %s: %s", site.uuid, exc)
            return False
        if pair is None:
            return True  # снимок один — сравнивать не с чем
        change = describe_change(*pair)
        hit: float | None = None
        entry = None
        record_low = False
        if change.kind == "price" and change.new_price is not None:
            targets = self._store.get(KEY_TARGETS)
            entry = targets.get(site.uuid) if isinstance(targets, dict) else None
            reached, entry = check_target(entry, change.new_price)
            hit = float(entry["price"]) if reached and entry else None
            if change.old_price is not None and change.new_price < change.old_price:
                try:
                    earlier = [price for _, price in (await self._service.price_history(site.uuid))[:-1]]
                except AppError:
                    earlier = []
                record_low = len(earlier) >= 2 and change.new_price < min(earlier)
        sent = await self._send(render_site_change(site, change, self._currency, target_hit=hit, record_low=record_low), site)
        if sent and entry is not None:
            targets = dict(self._store.get(KEY_TARGETS) or {})
            targets[site.uuid] = entry
            await self._store.set(KEY_TARGETS, targets)
        return sent

    async def _send(self, text: str, site: Site | None) -> bool:
        try:
            if self._markup is None or site is None:
                await self._bot.send_message(self._admin_id, text)
            else:
                await self._bot.send_message(self._admin_id, text, reply_markup=self._markup(site))
        except TelegramAPIError as exc:
            log.warning("Cannot deliver sites notification: %s", exc)
            return False
        return True
