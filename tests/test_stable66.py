"""6.6: новые разделы панели закрыты для гостей, все маршруты на месте."""

from __future__ import annotations

import pytest

pytest.importorskip("aiohttp.web")

from homebot.web import api

NEW = ("/api/setup", "/api/settings/general", "/api/launcher/status", "/api/modules", "/api/launcher")


def test_guest_cannot_reach_admin_sections():
    for path in NEW:
        assert api._guest_feature(path) is None


def test_routes_registered():
    paths = {getattr(r, "path", None) for r in api.routes}
    assert set(NEW) <= paths
