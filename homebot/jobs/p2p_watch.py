"""💱 Следит за P2P-курсом Binance и сообщает: о резком изменении, о достижении цели и вечерним итогом дня."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from homebot.services.apps_http import AppError
from homebot.services.p2p import (
    HISTORY_KEY,
    STATE_KEY,
    P2PQuote,
    P2PService,
    add_point,
    change_percent,
    crossed_target,
    stats,
)
from homebot.services.prefs import P2PPrefs
from homebot.services.quiet import Importance, set_importance
from homebot.services.state import StateStore
from homebot.ui.p2p_texts import render_p2p_change, render_p2p_summary, render_p2p_target

log = logging.getLogger(__name__)

KEY_STATE = STATE_KEY  # {baseline: последняя сообщённая цена, armed: цель взведена, summary_day: день последней сводки}
ERROR_AFTER = 6  # столько запросов подряд не удалось — сообщаем (при шаге в 5 минут это полчаса)


class P2PWatchJob:
    def __init__(
        self,
        *,
        bot: Bot,
        admin_id: int,
        service: P2PService,
        store: StateStore,
        prefs: Callable[[], P2PPrefs],
        tz: tzinfo,
        interval: int = 300,
        summary_hour: int = 21,
        markup: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._bot = bot
        self._admin_id = admin_id
        self._service = service
        self._store = store
        self._prefs = prefs
        self._tz = tz
        self._interval = interval
        self._summary_hour = summary_hour
        self._markup = markup
        self._clock = clock
        self._failures = 0

    async def run(self) -> None:
        set_importance(Importance.NORMAL)  # 🌙 ночью без звука: курс не срочный
        log.info("P2P watch running (every %s s)", self._interval)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("P2P check failed")
            await asyncio.sleep(self._interval)

    async def tick(self) -> P2PQuote | None:
        try:
            quote = await self._service.quote()
        except AppError as exc:
            self._failures += 1
            log.warning("P2P: %s", exc)
            if self._failures == ERROR_AFTER:
                await self._send(f"⚠️ P2P-курс не обновляется уже {ERROR_AFTER} проверок подряд: {exc}")
            return None
        if self._failures >= ERROR_AFTER:
            await self._send("✅ P2P-курс снова обновляется")
        self._failures = 0

        history = add_point(self._store.get(HISTORY_KEY), quote)
        await self._store.set(HISTORY_KEY, history)
        prefs = self._prefs()
        state = self._store.get(KEY_STATE)
        state = dict(state) if isinstance(state, dict) else {}
        asset, fiat = self._service.asset, self._service.fiat

        baseline = state.get("baseline")
        if not isinstance(baseline, int | float) or not prefs.threshold:
            state["baseline"] = quote.typical  # первый запуск или уведомления выключены: точка отсчёта «сейчас»
        elif abs(change_percent(baseline, quote.typical)) >= prefs.threshold:
            if await self._send(render_p2p_change(quote, float(baseline), asset=asset, fiat=fiat)):
                state["baseline"] = quote.typical

        notify, armed = crossed_target(prefs.target, quote.typical, bool(state.get("armed", True)))
        if notify and await self._send(render_p2p_target(quote, prefs.target, asset=asset, fiat=fiat)):
            armed = False
        elif notify:
            armed = True  # не доставили — сообщим при следующей проверке
        state["armed"] = armed

        local = datetime.fromtimestamp(self._clock(), self._tz)
        today = local.date().isoformat()
        if prefs.summary and local.hour >= self._summary_hour and state.get("summary_day") != today:
            day = stats(history, now=quote.at)
            if await self._send(render_p2p_summary(quote, day, asset=asset, fiat=fiat)):
                state["summary_day"] = today

        await self._store.set(KEY_STATE, state)
        return quote

    async def _send(self, text: str) -> bool:
        try:
            if self._markup is None:
                await self._bot.send_message(self._admin_id, text)
            else:
                await self._bot.send_message(self._admin_id, text, reply_markup=self._markup())
        except TelegramAPIError as exc:
            log.warning("Cannot deliver P2P notification: %s", exc)
            return False
        return True
