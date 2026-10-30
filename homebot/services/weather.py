"""Погода из Open-Meteo: коды WMO и разбор ответа (без сетевого ввода-вывода)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from typing import Any

# WMO weather interpretation codes -> (эмодзи, описание)
_WMO: dict[int, tuple[str, str]] = {
    0: ("☀️", "ясно"),
    1: ("🌤", "преимущественно ясно"),
    2: ("⛅", "переменная облачность"),
    3: ("☁️", "пасмурно"),
    45: ("🌫", "туман"),
    48: ("🌫", "изморозь"),
    51: ("🌦", "лёгкая морось"),
    53: ("🌦", "морось"),
    55: ("🌦", "сильная морось"),
    56: ("🌧", "ледяная морось"),
    57: ("🌧", "сильная ледяная морось"),
    61: ("🌧", "небольшой дождь"),
    63: ("🌧", "дождь"),
    65: ("🌧", "сильный дождь"),
    66: ("🌧", "ледяной дождь"),
    67: ("🌧", "сильный ледяной дождь"),
    71: ("🌨", "небольшой снег"),
    73: ("🌨", "снег"),
    75: ("❄️", "сильный снег"),
    77: ("🌨", "снежная крупа"),
    80: ("🌦", "ливни"),
    81: ("🌧", "сильные ливни"),
    82: ("⛈", "очень сильные ливни"),
    85: ("🌨", "снегопад"),
    86: ("❄️", "сильный снегопад"),
    95: ("⛈", "гроза"),
    96: ("⛈", "гроза с градом"),
    99: ("⛈", "сильная гроза с градом"),
}

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


_WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


@dataclass(frozen=True, slots=True)
class DayForecast:
    date: str  # YYYY-MM-DD
    code: int
    t_max: float
    t_min: float
    precip_prob: int | None

    @property
    def emoji(self) -> str:
        return _WMO.get(self.code, ("🌡", ""))[0]

    @property
    def weekday(self) -> str:
        try:
            year, month, day = (int(x) for x in self.date.split("-"))
            return _WEEKDAYS_SHORT[date_cls(year, month, day).weekday()]
        except ValueError:
            return self.date[-5:]


@dataclass(frozen=True, slots=True)
class WeatherInfo:
    temperature: float
    feels_like: float
    wind_ms: float
    code: int
    t_max: float
    t_min: float
    precip_prob: int | None
    forecast: tuple[DayForecast, ...] = ()  # следующие дни (без сегодняшнего)

    @property
    def emoji(self) -> str:
        return _WMO.get(self.code, ("🌡", ""))[0]

    @property
    def description(self) -> str:
        return _WMO.get(self.code, ("", f"код погоды {self.code}"))[1]


def build_params(lat: float, lon: float) -> dict[str, str]:
    return {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "wind_speed_unit": "ms",
        "timezone": "auto",
        "forecast_days": "4",
    }


def parse_open_meteo(data: dict[str, Any]) -> WeatherInfo:
    """Бросает KeyError/IndexError/TypeError/ValueError, если ответ не того формата."""
    current = data["current"]
    daily = data["daily"]
    probs = daily.get("precipitation_probability_max") or [None]
    prob = probs[0]
    forecast: list[DayForecast] = []
    dates = daily.get("time") or []
    codes = daily.get("weather_code") or []
    for i in range(1, min(len(dates), len(codes), len(daily["temperature_2m_max"]), len(daily["temperature_2m_min"]))):
        p = probs[i] if i < len(probs) else None
        forecast.append(
            DayForecast(
                date=str(dates[i]),
                code=int(codes[i]),
                t_max=float(daily["temperature_2m_max"][i]),
                t_min=float(daily["temperature_2m_min"][i]),
                precip_prob=int(p) if p is not None else None,
            )
        )
    return WeatherInfo(
        temperature=float(current["temperature_2m"]),
        feels_like=float(current["apparent_temperature"]),
        wind_ms=float(current["wind_speed_10m"]),
        code=int(current["weather_code"]),
        t_max=float(daily["temperature_2m_max"][0]),
        t_min=float(daily["temperature_2m_min"][0]),
        precip_prob=int(prob) if prob is not None else None,
        forecast=tuple(forecast),
    )
