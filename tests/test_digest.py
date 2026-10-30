import asyncio
from datetime import UTC, datetime

from homebot.services.brief import Brief
from homebot.services.digest import DigestService
from homebot.services.system import DiskInfo, SystemSnapshot, TempReading
from homebot.services.units import UnitStatus
from homebot.ui.texts import render_brief, render_status
from tests.fakes import FakeResponse, FakeSession

WEATHER = {
    "current": {"temperature_2m": 12.0, "apparent_temperature": 10.0, "weather_code": 3, "wind_speed_10m": 2.0},
    "daily": {"temperature_2m_max": [15], "temperature_2m_min": [-0.3], "precipitation_probability_max": [10]},
}


def snapshot(**overrides) -> SystemSnapshot:
    base = {
        "taken_at": datetime(2026, 10, 13, 8, 0, tzinfo=UTC),
        "cpu_percent": 12.0, "cpu_threads": 8, "load_avg": (0.1, 0.2, 0.3),
        "ram_total": 16 * 1024**3, "ram_used": 4 * 1024**3, "ram_percent": 25.0,
        "swap_total": 0, "swap_used": 0, "swap_percent": 0.0,
        "disks": (DiskInfo("/", 500 * 1024**3, 100 * 1024**3, 20.0),),
        "temperature": TempReading("coretemp", "Package id 0", 48.0, 100.0),
        "uptime_sec": 93_784.0,
    }  # fmt: skip
    base.update(overrides)
    return SystemSnapshot(**base)


class FakeSystem:
    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    async def snapshot(self) -> SystemSnapshot:
        if self._fail:
            raise RuntimeError("psutil exploded")
        return snapshot()


class FakeUnits:
    async def status_all(self):
        return [UnitStatus("ssh", "loaded", "active", "running"), UnitStatus("ollama", "loaded", "failed", "failed")]


def _service(handler, *, coords=(50.45, 30.52), system=None) -> DigestService:
    return DigestService(
        FakeSession(handler), system or FakeSystem(), FakeUnits(),  # type: ignore[arg-type]
        tz=UTC, weather_coords=coords, weather_label="Киев",
    )  # fmt: skip


def _text(report) -> str:
    return render_brief(Brief(datetime(2026, 10, 13, 8, 0, tzinfo=UTC), report), UTC)


def test_digest_survives_partial_failures():
    def handler(method, url, kwargs):
        if "open-meteo" in url:
            return FakeResponse(status=500)
        raise AssertionError(f"unexpected url {url}")

    report = asyncio.run(_service(handler).build())
    assert report.system is not None and len(report.services) == 2
    assert report.weather is None and report.weather_error == "HTTP 500"
    text = _text(report)
    assert "погода недоступна" in text
    assert "ssh" in text and "🔴" in text  # упавшая служба видна в сводке


def test_system_failure_does_not_break_digest():
    service = _service(lambda *a: FakeResponse(body=WEATHER), system=FakeSystem(fail=True))
    report = asyncio.run(service.build())
    assert report.system is None
    assert report.weather is not None
    assert "метрики недоступны" in _text(report)


def test_weather_disabled_when_no_coordinates():
    calls = []

    def handler(method, url, kwargs):
        calls.append(url)
        return FakeResponse(body=WEATHER)

    report = asyncio.run(_service(handler, coords=None).build())
    assert report.weather is None and report.weather_error is None and calls == []
    assert "Киев" not in _text(report)  # раздела погоды нет вообще


def test_negative_zero_temperature_is_not_rendered_as_minus_zero():
    report = asyncio.run(_service(lambda *a: FakeResponse(body=WEATHER)).build())
    text = _text(report)
    assert "-0°C" not in text and "0°C" in text


def test_render_status_escapes_and_handles_missing_sensors():
    text = render_status(snapshot(temperature=None, load_avg=None, disks=(DiskInfo("/mnt/<x>&y", 10, 5, 50.0),)))
    assert "/mnt/&lt;x&gt;&amp;y" in text
    assert "Температура" not in text and "Load" not in text and "Swap" not in text
