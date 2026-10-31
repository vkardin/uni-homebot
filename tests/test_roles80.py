"""8.0: роли людей — готовые наборы прав, формат state.json прежний."""

from __future__ import annotations

import asyncio

from homebot.services.access import APPROVED, DEFAULT_PERMS, FEATURES, ROLES, AccessService, role_of
from homebot.services.state import StateStore


def test_roles(tmp_path):
    store = StateStore(tmp_path / "s.json")
    store.load()
    acc = AccessService(store, 1)
    asyncio.run(acc.register(5, "Мама", None))
    assert role_of(DEFAULT_PERMS) == ""
    p = asyncio.run(acc.set_role(5, "family"))
    assert p is not None and set(p.perms) == set(ROLES["family"][1]) and role_of(p.perms) == "family"
    assert all(f in FEATURES for _, perms in ROLES.values() for f in perms)
    assert asyncio.run(acc.set_role(5, "boss")) is None and asyncio.run(acc.set_role(99, "guest")) is None
    raw = store.get("access")["5"]
    assert isinstance(raw["perms"], list)  # прежний формат


def test_people_api(tmp_path):
    import pytest

    pytest.importorskip("aiohttp.web")
    from types import SimpleNamespace

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
    acc = AccessService(store, 1)
    asyncio.run(acc.register(7, "Друг", "friend"))
    ctx = SimpleNamespace(access=acc)
    st = asyncio.run(api.people_get(Req(ctx)))
    assert st["people"][0]["status"] == "pending"
    st = asyncio.run(api.people_post(Req(ctx, {"id": 7, "op": "approve"})))
    st = asyncio.run(api.people_post(Req(ctx, {"id": 7, "op": "role", "role": "guest"})))
    assert st["people"][0]["role"] == "guest" and acc.get(7).status == APPROVED  # type: ignore[union-attr]
    with pytest.raises(api.ApiError):
        asyncio.run(api.people_post(Req(ctx, {"id": 8, "op": "approve"})))
    assert api._guest_feature("/api/people") is None
    assert asyncio.run(api.people_get(Req(SimpleNamespace(access=None))))["enabled"] is False
