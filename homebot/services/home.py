"""Данные для стартового экрана: погода, курс, нагрузка сервера, пара свежих новостей, расход месяца.

Всё сетевое кэшируется и обновляется фоновой задачей, поэтому меню открывается мгновенно. Если источник
недоступен, строка просто становится короче — меню из-за этого не ломается и не ждёт.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp

from homebot.services import weather
from homebot.services.rates import Rate, fetch_rates
from homebot.services.weather import WeatherInfo

log = logging.getLogger(__name__)

WEATHER_TTL = 15 * 60
RATES_TTL = 60 * 60
MONEY_TTL = 10 * 60
STALE_LIMIT = 3 * 3600  # старше — лучше ничего, чем устаревшее
FETCH_TIMEOUT = 4
NEWS_COUNT = 3
METRICS_WINDOW = 600


@dataclass(frozen=True, slots=True)
class HomeData:
    weather: WeatherInfo | None = None
    rates: tuple[Rate, ...] = ()
    cpu: float | None = None
    ram: float | None = None
    news: tuple[str, ...] = ()
    month_spent: int | None = None  # копейки за текущий месяц (Actual)


class _Slot:
    """Значение с временем получения: свежее — отдаём, протухшее — обновляем, сломалось — держим старое."""

    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self.value: Any = None
        self.at = 0.0

    def fresh(self, now: float) -> bool:
        return self.value is not None and now - self.at < self.ttl

    def usable(self, now: float) -> Any:
        return self.value if self.value is not None and now - self.at < STALE_LIMIT else None


class HomeService:
    def __init__(
        self,
        *,
        session: aiohttp.ClientSession | None,
        db: Any,
        system: Any,
        coords: tuple[float, float] | None,
        currencies: Sequence[str],
        month_spent: Callable[[], Awaitable[int | None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        fetch_weather: Callable[[], Awaitable[WeatherInfo]] | None = None,
        fetch_rates_fn: Callable[[], Awaitable[list[Rate]]] | None = None,
    ) -> None:
        self._session = session
        self._db = db
        self._system = system
        self._coords = coords
        self._currencies = list(currencies)
        self._month_spent = month_spent
        self._clock = clock
        self._weather = _Slot(WEATHER_TTL)
        self._rates = _Slot(RATES_TTL)
        self._money = _Slot(MONEY_TTL)
        self._fetch_weather = fetch_weather or self._default_weather
        self._fetch_rates = fetch_rates_fn or self._default_rates

    # ------------------------------------------------------------- источники
    async def _default_weather(self) -> WeatherInfo:
        assert self._session is not None and self._coords is not None
        lat, lon = self._coords
        async with self._session.get(
            weather.OPEN_METEO_URL, params=weather.build_params(lat, lon), timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
        ) as resp:
            resp.raise_for_status()
            return weather.parse_open_meteo(await resp.json(content_type=None))

    async def _default_rates(self) -> list[Rate]:
        assert self._session is not None
        return await fetch_rates(self._session, self._currencies)

    async def _refresh(self, slot: _Slot, loader: Callable[[], Awaitable[Any]], name: str, *, force: bool) -> None:
        now = self._clock()
        if slot.fresh(now) and not force:
            return
        try:
            value = await asyncio.wait_for(loader(), FETCH_TIMEOUT + 1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # любая беда источника — не повод ломать меню
            log.debug("Home: %s unavailable: %s", name, exc)
            return
        if value is not None:
            slot.value, slot.at = value, self._clock()

    async def refresh(self, *, force: bool = False) -> None:
        """Обновить то, что устарело (фоновая задача зовёт с force=True)."""
        jobs = []
        if self._coords is not None:
            jobs.append(self._refresh(self._weather, self._fetch_weather, "weather", force=force))
        if self._currencies:
            jobs.append(self._refresh(self._rates, self._fetch_rates, "rates", force=force))
        if self._month_spent is not None:
            jobs.append(self._refresh(self._money, self._month_spent, "finance", force=force))
        await asyncio.gather(*jobs)

    async def run(self, interval: int = 600) -> None:
        log.info("Home summary refresh running (every %s s)", interval)
        while True:
            try:
                await self.refresh(force=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Home summary refresh failed")
            await asyncio.sleep(interval)

    # ------------------------------------------------------------- сборка
    async def _load(self) -> tuple[float | None, float | None]:
        try:
            rows = await self._db.metrics_since(int(time.time()) - METRICS_WINDOW)
        except Exception as exc:
            log.debug("Home: metrics unavailable: %s", exc)
            rows = []
        if rows:
            return rows[-1].cpu, rows[-1].ram
        try:
            snap = await self._system.snapshot()
        except Exception as exc:
            log.debug("Home: snapshot failed: %s", exc)
            return None, None
        return snap.cpu_percent, snap.ram_percent

    async def _news(self) -> tuple[str, ...]:
        try:
            rows = await self._db.recent_sent_news(NEWS_COUNT)
        except Exception as exc:
            log.debug("Home: news unavailable: %s", exc)
            return ()
        return tuple(r.title for r in rows if r.title)

    async def data(self, *, wait: float = 2.5) -> HomeData:
        """Снимок для меню. Сеть ждём не дольше `wait` секунд — потом показываем то, что есть."""
        pending = asyncio.ensure_future(self.refresh())
        try:
            await asyncio.wait_for(asyncio.shield(pending), wait)
        except TimeoutError:
            pass  # обновление доделается в фоне, к следующему открытию меню
        (cpu, ram), news = await asyncio.gather(self._load(), self._news())
        now = self._clock()
        return HomeData(
            weather=self._weather.usable(now),
            rates=tuple(self._rates.usable(now) or ()),
            cpu=cpu,
            ram=ram,
            news=news,
            month_spent=self._money.usable(now),
        )


def short(text: str, limit: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0].rstrip(",.:;—-") + "…"
