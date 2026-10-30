"""Умная погода: сообщаем только когда есть повод — скоро дождь, заморозки, жара, сильный ветер.

Источник — Open-Meteo (тот же, что в сводке). Осадки берём по 15 минут. Вне Центральной Европы и
Северной Америки Open-Meteo считает их интерполяцией почасового прогноза, так что «через 20 минут» —
оценка, а не радар. Поэтому порог осадков низкий, а повторы ограничены (см. COOLDOWN_*).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from homebot.services.apps_http import AppError, request
from homebot.services.weather import OPEN_METEO_URL

SLOT = timedelta(minutes=15)
RAIN_LOOKAHEAD = timedelta(minutes=75)
RAIN_SLOT_MM = 0.2  # за 15 минут: морось и выше
RAIN_TOTAL_MM = 0.4  # за ближайший час
WET_NOW_MM = 0.1
HOURS_AHEAD = 12
COOLDOWN_RAIN = 3 * 3600
COOLDOWN_OTHER = 20 * 3600


@dataclass(frozen=True, slots=True)
class Nowcast:
    rain: tuple[tuple[datetime, float], ...]  # (конец 15-минутки, мм)
    hourly: tuple[tuple[datetime, float, float], ...]  # (час, °C, порывы м/с)


@dataclass(frozen=True, slots=True)
class WeatherEvent:
    kind: str  # rain | frost | heat | wind
    value: float  # rain: минут до начала; остальное: °C или м/с
    at: datetime | None = None
    extra: float = 0.0  # rain: мм за ближайший час


@dataclass(frozen=True, slots=True)
class Thresholds:
    frost_c: float = -5.0
    heat_c: float = 35.0
    gust_ms: float = 17.0


def build_nowcast_params(lat: float, lon: float) -> dict[str, str]:
    return {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "minutely_15": "precipitation",
        "hourly": "temperature_2m,wind_gusts_10m",
        "past_minutely_15": "2",
        "forecast_minutely_15": "8",
        "forecast_hours": str(HOURS_AHEAD),
        "wind_speed_unit": "ms",
        "timezone": "auto",
    }


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def parse_nowcast(data: Any) -> Nowcast:
    """Бросает AppError, если ответ не того формата. Пропуски (null) в рядах просто пропускаются."""
    try:
        offset = timezone(timedelta(seconds=int(data.get("utc_offset_seconds") or 0)))
        m_times, m_rain = data["minutely_15"]["time"], data["minutely_15"]["precipitation"]
        h_times = data["hourly"]["time"]
        h_temp, h_gust = data["hourly"]["temperature_2m"], data["hourly"]["wind_gusts_10m"]
        rain = [
            (datetime.fromisoformat(t).replace(tzinfo=offset), mm)
            for t, raw in zip(m_times, m_rain, strict=False)
            if (mm := _num(raw)) is not None
        ]
        hourly = [
            (datetime.fromisoformat(t).replace(tzinfo=offset), temp, gust)
            for t, raw_t, raw_g in zip(h_times, h_temp, h_gust, strict=False)
            if (temp := _num(raw_t)) is not None and (gust := _num(raw_g)) is not None
        ]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AppError("Open-Meteo ответил в неожиданном формате") from exc
    return Nowcast(tuple(sorted(rain)), tuple(sorted(hourly)))


def evaluate(nc: Nowcast, now: datetime, limits: Thresholds | None = None) -> list[WeatherEvent]:
    """Что стоит сообщить прямо сейчас (без учёта «уже говорили» — это дело задачи)."""
    limits = limits or Thresholds()
    events: list[WeatherEvent] = []

    wet_now = any(mm >= WET_NOW_MM for t, mm in nc.rain if now - 2 * SLOT <= t <= now)
    if not wet_now:
        ahead = [(t, mm) for t, mm in nc.rain if now < t <= now + RAIN_LOOKAHEAD]
        hour = sum(mm for t, mm in ahead if t <= now + timedelta(hours=1))
        first = next(((t, mm) for t, mm in ahead if mm >= RAIN_SLOT_MM), None)
        if first is not None and hour >= RAIN_TOTAL_MM:
            starts = (first[0] - SLOT - now).total_seconds() / 60  # интервал считается «до» отметки времени
            events.append(WeatherEvent("rain", max(5.0, round(starts / 5) * 5), first[0] - SLOT, round(hour, 1)))

    window = [row for row in nc.hourly if now - timedelta(hours=1) < row[0] <= now + timedelta(hours=HOURS_AHEAD)]
    if window:
        coldest = min(window, key=lambda r: r[1])
        hottest = max(window, key=lambda r: r[1])
        gustiest = max(window, key=lambda r: r[2])
        if coldest[1] <= limits.frost_c:
            events.append(WeatherEvent("frost", coldest[1], coldest[0]))
        if hottest[1] >= limits.heat_c:
            events.append(WeatherEvent("heat", hottest[1], hottest[0]))
        if gustiest[2] >= limits.gust_ms:
            events.append(WeatherEvent("wind", gustiest[2], gustiest[0]))
    return events


def cooldown_for(kind: str) -> int:
    return COOLDOWN_RAIN if kind == "rain" else COOLDOWN_OTHER


def due_events(events: Sequence[WeatherEvent], last_sent: dict[str, float], now_ts: float) -> list[WeatherEvent]:
    """Оставляет события, о которых давно не говорили."""
    return [e for e in events if e.kind not in last_sent or now_ts - float(last_sent[e.kind]) >= cooldown_for(e.kind)]


async def fetch_nowcast(session: aiohttp.ClientSession, lat: float, lon: float) -> Nowcast:
    data = await request(session, "GET", OPEN_METEO_URL, params=build_nowcast_params(lat, lon), timeout=20)
    return parse_nowcast(data)
