import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError

from homebot.jobs.weather_watch import WeatherAlertsJob
from homebot.services.apps_http import AppError
from homebot.services.prefs import NightPrefs
from homebot.services.state import StateStore
from homebot.services.weather_alerts import (
    Nowcast,
    Thresholds,
    build_nowcast_params,
    due_events,
    evaluate,
    fetch_nowcast,
    parse_nowcast,
)
from homebot.ui.extra_texts import render_weather_events
from tests.fakes import FakeResponse, FakeSession

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 10, 13, 13, 0, tzinfo=UTC)  # 16:00 по Киеву
Q = timedelta(minutes=15)


def nc(rain=(), temps=None, gusts=None):
    """rain — мм по 15-минуткам начиная с now−30 мин; temps/gusts — по часам начиная с now."""
    slots = tuple((NOW - 2 * Q + i * Q, mm) for i, mm in enumerate(rain))
    temps = temps or [10.0] * 12
    gusts = gusts or [5.0] * 12
    hourly = tuple((NOW + timedelta(hours=i), t, g) for i, (t, g) in enumerate(zip(temps, gusts, strict=True)))
    return Nowcast(slots, hourly)


def test_rain_soon_when_dry_now():
    events = evaluate(nc(rain=[0, 0, 0, 0, 0.3, 0.6, 0.4, 0.1, 0, 0]), NOW)
    assert [e.kind for e in events] == ["rain"]
    rain = events[0]
    assert 15 <= rain.value <= 35 and 1.2 <= rain.extra <= 1.4  # слот «+60» считается «до», начало ~ через полчаса


def test_no_rain_alert_when_already_raining_or_too_light_or_too_far():
    assert evaluate(nc(rain=[0.4, 0.5, 0.6, 0.5, 0.4, 0, 0, 0, 0, 0]), NOW) == []  # уже идёт
    assert evaluate(nc(rain=[0, 0, 0, 0.1, 0.1, 0.1, 0.1, 0, 0, 0]), NOW) == []  # морось ниже порога
    assert evaluate(nc(rain=[0, 0, 0, 0, 0, 0, 0, 0, 0.9, 0.9]), NOW) == []  # дальше чем через 75 минут
    assert evaluate(nc(rain=[0] * 10), NOW) == []


def test_frost_heat_wind_use_the_next_12_hours():
    frost = evaluate(nc(temps=[2, 0, -2, -4, -6.5, -7, -3, 0, 2, 3, 4, 5]), NOW)
    assert [e.kind for e in frost] == ["frost"] and frost[0].value == -7 and frost[0].at == NOW + timedelta(hours=5)
    assert evaluate(nc(temps=[2] * 12), NOW) == []
    hot = evaluate(nc(temps=[30, 33, 36, 37, 35, 32, 30, 28, 26, 25, 24, 23]), NOW)
    assert [e.kind for e in hot] == ["heat"] and hot[0].value == 37
    windy = evaluate(nc(gusts=[5, 8, 12, 19, 22, 18, 10, 5, 4, 4, 4, 4]), NOW)
    assert [e.kind for e in windy] == ["wind"] and windy[0].value == 22
    custom = evaluate(nc(temps=[-1] * 12), NOW, Thresholds(frost_c=-1))
    assert [e.kind for e in custom] == ["frost"]
    assert evaluate(Nowcast((), ()), NOW) == []


def test_due_events_respects_cooldowns():
    events = evaluate(nc(rain=[0, 0, 0, 0, 0.5, 0.5, 0, 0, 0, 0], temps=[-9] * 12), NOW)
    kinds = {e.kind for e in events}
    assert kinds == {"rain", "frost"}
    ts = NOW.timestamp()
    assert {e.kind for e in due_events(events, {}, ts)} == kinds
    assert {e.kind for e in due_events(events, {"rain": ts - 3600, "frost": ts - 3600}, ts)} == set()  # час назад — рано
    assert {e.kind for e in due_events(events, {"rain": ts - 4 * 3600, "frost": ts - 4 * 3600}, ts)} == {
        "rain"
    }  # дождь можно, мороз нет
    assert {e.kind for e in due_events(events, {"frost": ts - 21 * 3600}, ts)} == kinds


PAYLOAD = {
    "utc_offset_seconds": 10800,
    "minutely_15": {
        "time": ["2026-10-13T15:30", "2026-10-13T15:45", "2026-10-13T16:00", "2026-10-13T16:15"],
        "precipitation": [0.0, 0.0, None, 0.5],
    },
    "hourly": {
        "time": ["2026-10-13T16:00", "2026-10-13T17:00"],
        "temperature_2m": [12.5, None],
        "wind_gusts_10m": [8.0, 9.0],
    },
}


def test_parse_nowcast_applies_utc_offset_and_skips_gaps():
    parsed = parse_nowcast(PAYLOAD)
    assert [mm for _, mm in parsed.rain] == [0.0, 0.0, 0.5]
    assert parsed.rain[0][0] == datetime(2026, 10, 13, 12, 30, tzinfo=UTC)  # 15:30 по UTC+3
    assert len(parsed.hourly) == 1 and parsed.hourly[0][1:] == (12.5, 8.0)
    for bad in (None, {}, {"minutely_15": {}}, {"minutely_15": {"time": 5, "precipitation": 5}, "hourly": {}}):
        try:
            parse_nowcast(bad)
        except AppError:
            pass
        else:
            raise AssertionError("ожидали AppError")


def test_params_and_fetch_use_open_meteo_nowcast_query():
    params = build_nowcast_params(47.9105, 33.3918)
    assert params["minutely_15"] == "precipitation" and params["timezone"] == "auto" and params["wind_speed_unit"] == "ms"
    assert all(isinstance(v, str) for v in params.values())

    async def scenario():
        session = FakeSession(lambda m, u, k: FakeResponse(body=PAYLOAD))
        result = await fetch_nowcast(session, 47.9, 33.4)  # type: ignore[arg-type]
        assert result.rain and "open-meteo" in session.calls[0][1]

    asyncio.run(scenario())


class FakeBot:
    def __init__(self):
        self.sent: list[str] = []
        self.fail = False

    async def send_message(self, chat_id, text):
        if self.fail:
            raise TelegramAPIError(method=None, message="offline")  # type: ignore[arg-type]
        self.sent.append(text)
        return SimpleNamespace(message_id=1)


class FakePrefs:
    def __init__(self, quiet=False):
        self.quiet = quiet

    def night(self):
        return NightPrefs(enabled=self.quiet, start=0, end=23)  # «ночь» почти весь день


def test_weather_job_sends_once_per_cooldown_and_respects_quiet_hours_and_failures():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            bot = FakeBot()
            data = {"nc": nc(rain=[0, 0, 0, 0, 0.5, 0.5, 0, 0, 0, 0])}

            async def fetch():
                return data["nc"]

            job = WeatherAlertsJob(bot=bot, admin_id=1, store=store, prefs=FakePrefs(), fetch=fetch, tz=KYIV)  # type: ignore[arg-type]
            assert [e.kind for e in await job.tick(NOW)] == ["rain"]
            assert "Скоро дождь" in bot.sent[0]
            assert await job.tick(NOW + timedelta(minutes=15)) == []  # то же самое — молчим
            bot.fail = True
            data["nc"] = nc(temps=[-9] * 12)
            assert await job.tick(NOW + timedelta(hours=4)) == []  # не доставили...
            bot.fail = False
            assert [e.kind for e in await job.tick(NOW + timedelta(hours=4))] == ["frost"]  # ...значит повторим
            assert "Мороз" in bot.sent[-1]

            quiet = WeatherAlertsJob(
                bot=bot, admin_id=1, store=StateStore(Path(tmp) / "q.json"), prefs=FakePrefs(quiet=True), fetch=fetch, tz=KYIV  # type: ignore[arg-type]
            )  # fmt: skip
            assert await quiet.tick(NOW) == []  # тихие часы

    asyncio.run(scenario())


def test_render_weather_events_lines():
    events = evaluate(nc(rain=[0, 0, 0, 0, 0.5, 0.5, 0, 0, 0, 0], temps=[-9] * 12, gusts=[20] * 12), NOW)
    text = render_weather_events(events, KYIV)
    assert text.count("\n") == 2 and "☂️" in text and "-9 °C" in text and "м/с" in text
    soon = evaluate(nc(rain=[0, 0, 0, 0.5, 0.6, 0, 0, 0, 0, 0]), NOW)
    assert "вот-вот" in render_weather_events(soon, KYIV)
