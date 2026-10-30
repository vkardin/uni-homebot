"""6.2: настройки из панели — settings.json, .env главнее."""

from __future__ import annotations

import pytest

from homebot.config import Settings
from homebot.services import overrides


def _settings(**kw):
    return Settings(bot_token="1:x", admin_id=1, _env_file=None, **kw)  # type: ignore[call-arg]


def test_validate():
    assert overrides.validate("timezone", " Europe/Berlin ") == "Europe/Berlin"
    assert overrides.validate("weather_lat", "50.5") == 50.5
    assert overrides.validate("weather_lat", "") is None
    assert overrides.validate("games_country", "de") == "DE"
    assert overrides.validate("backup_hour", "3") == 3
    for name, bad in [("timezone", "Mars/Base"), ("weather_lat", "200"), ("games_country", "Germany"), ("backup_hour", "25"),
                      ("alert_cpu_percent", "abc"), ("bot_token", "x")]:  # fmt: skip
        with pytest.raises(ValueError):
            overrides.validate(name, bad)


def test_apply_env_wins(tmp_path):
    overrides.save(tmp_path, {"timezone": "Europe/Berlin", "currency": "€", "backup_hour": 2, "weather_lat": "bad", "junk": 1})
    data = overrides.load(tmp_path)
    assert "junk" not in data
    s = overrides.apply(_settings(backup_hour=5), data)
    assert s.timezone == "Europe/Berlin" and s.currency == "€" and s.actual_currency == "€" and s.sites_currency == "€"
    assert s.backup_hour == 5  # .env главнее
    assert s.weather_lat is None  # ошибочное пропущено
    assert "timezone" not in s.model_fields_set
    rows = {r["name"]: r for r in overrides.describe(s, data)}
    assert (
        rows["backup_hour"]["source"] == "env" and rows["timezone"]["source"] == "panel" and rows["weather_lon"]["source"] == "default"
    )


def test_broken_file(tmp_path):
    (tmp_path / "settings.json").write_text("{oops", encoding="utf-8")
    assert overrides.load(tmp_path) == {}
    assert overrides.apply(_settings(), {}) is not None


def test_panel_endpoint(tmp_path):
    pytest.importorskip("aiohttp.web")
    import asyncio
    from types import SimpleNamespace

    from homebot.services.state import StateStore
    from homebot.web import api

    class Req(dict):
        def __init__(self, ctx, body=None):
            super().__init__()
            self.app = {"ctx": ctx}
            self._body = body

        async def json(self):
            return self._body

    store = StateStore(tmp_path / "s.json")
    store.load()
    ctx = SimpleNamespace(settings=_settings(state_dir=tmp_path, currency="$"), store=store)
    st = asyncio.run(api.general_post(Req(ctx, {"op": "set", "name": "timezone", "value": "Asia/Tokyo"})))
    assert st["pending"] and overrides.load(tmp_path) == {"timezone": "Asia/Tokyo"}
    with pytest.raises(api.ApiError):  # задано в .env
        asyncio.run(api.general_post(Req(ctx, {"op": "set", "name": "currency", "value": "€"})))
    with pytest.raises(api.ApiError):
        asyncio.run(api.general_post(Req(ctx, {"op": "set", "name": "timezone", "value": "Nope/Nope"})))
    asyncio.run(api.general_post(Req(ctx, {"op": "reset", "name": "timezone"})))
    assert overrides.load(tmp_path) == {}
