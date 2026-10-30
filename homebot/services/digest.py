"""Сводка о сервере и погоде для часовой сводки: состояние сервера, службы, погода, курсы НБУ, обновления, SMART.

Каждый источник независим: если погода или курсы недоступны, остальное всё равно собирается,
а проблемный раздел помечается ошибкой.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, tzinfo

import aiohttp

from homebot.services import weather
from homebot.services.rates import Rate, fetch_rates
from homebot.services.smart import SmartService, smart_issues
from homebot.services.system import SystemService, SystemSnapshot
from homebot.services.units import UnitsService, UnitStatus
from homebot.services.updates import UpdatesInfo, UpdatesService
from homebot.services.weather import WeatherInfo

log = logging.getLogger(__name__)

_WEATHER_TIMEOUT = aiohttp.ClientTimeout(total=10)


@dataclass(slots=True)
class Digest:
    created_at: datetime
    weather_label: str
    system: SystemSnapshot | None = None
    services: list[UnitStatus] = field(default_factory=list)
    weather: WeatherInfo | None = None
    weather_error: str | None = None
    rates: list[Rate] = field(default_factory=list)
    rates_error: str | None = None
    updates: UpdatesInfo | None = None
    smart_issues: list[str] | None = None  # None — SMART не настроен


def _short_error(exc: BaseException) -> str:
    if isinstance(exc, aiohttp.ClientResponseError):
        return f"HTTP {exc.status}"
    if isinstance(exc, TimeoutError):
        return "таймаут"
    return (str(exc) or type(exc).__name__)[:80]


class DigestService:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        system: SystemService,
        units: UnitsService,
        *,
        tz: tzinfo | None,
        weather_coords: tuple[float, float] | None,
        weather_label: str,
        currencies: Sequence[str] = (),
        updates: UpdatesService | None = None,
        smart: SmartService | None = None,
    ) -> None:
        self._session = session
        self._currencies = tuple(currencies)
        self._updates = updates
        self._smart = smart
        self._system = system
        self._units = units
        self._tz = tz
        self._coords = weather_coords
        self._label = weather_label

    async def build(self) -> Digest:
        system, services, (weather_info, weather_error), (rates, rates_error), updates = await asyncio.gather(
            self._safe_system(),
            self._units.status_all(),
            self._safe_weather(),
            self._safe_rates(),
            self._safe_updates(),
        )
        smart_report = self._smart.read() if self._smart is not None else None
        report = Digest(
            created_at=datetime.now(self._tz),
            weather_label=self._label,
            system=system,
            services=services,
            weather=weather_info,
            weather_error=weather_error,
            rates=rates,
            rates_error=rates_error,
            updates=updates,
            smart_issues=smart_issues(smart_report) if smart_report is not None else None,
        )
        return report

    # ------------------------------------------------------------- источники
    async def _safe_system(self) -> SystemSnapshot | None:
        try:
            return await self._system.snapshot()
        except Exception:  # psutil может упасть на экзотическом железе — дайджест не должен
            log.exception("Digest: cannot collect system snapshot")
            return None

    async def _safe_rates(self) -> tuple[list[Rate], str | None]:
        if not self._currencies:
            return [], None
        try:
            return await fetch_rates(self._session, self._currencies), None
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            log.warning("Digest: NBU rates unavailable: %s", exc)
            return [], _short_error(exc)

    async def _safe_updates(self) -> UpdatesInfo | None:
        if self._updates is None:
            return None
        try:
            return await self._updates.check()
        except Exception:
            log.exception("Digest: updates check failed")
            return None

    async def _safe_weather(self) -> tuple[WeatherInfo | None, str | None]:
        if self._coords is None:
            return None, None
        lat, lon = self._coords
        try:
            async with self._session.get(
                weather.OPEN_METEO_URL, params=weather.build_params(lat, lon), timeout=_WEATHER_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
            return weather.parse_open_meteo(data), None
        except (aiohttp.ClientError, TimeoutError, KeyError, IndexError, TypeError, ValueError) as exc:
            log.warning("Digest: weather unavailable: %s", exc)
            return None, _short_error(exc)
