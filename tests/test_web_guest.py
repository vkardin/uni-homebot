"""Панель для гостей: что им доступно в API (нужен aiohttp, иначе пропускается)."""

import asyncio
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("aiohttp.web")

from homebot.services.access import APPROVED, AccessService
from homebot.services.state import StateStore
from homebot.web.api import _guest_feature, auth_middleware
from homebot.web.auth import sign_for_tests

TOKEN, ADMIN = "123:abc", 1001


class Req(dict):
    def __init__(self, path, init, ctx):
        super().__init__()
        self.path, self.method = path, "GET"
        self.headers = {"Authorization": "tma " + init}
        self.app = {"ctx": ctx}


def signed(uid):
    return sign_for_tests({"auth_date": str(int(time.time())), "user": json.dumps({"id": uid})}, TOKEN)


def call(ctx, path, uid):
    async def handler(request):
        return {"guest": request.get("guest").id if request.get("guest") else None}

    response = asyncio.run(auth_middleware(Req(path, signed(uid), ctx), handler))
    return response.status if hasattr(response, "status") else 200, response


def test_guest_paths_mapping():
    assert _guest_feature("/api/notes") == "notes" and _guest_feature("/api/notes/5") == "notes"
    assert _guest_feature("/api/me") == "me" and _guest_feature("/api/p2p") == "p2p"
    assert _guest_feature("/api/me/settings") == "me"  # личные настройки — своё; хозяйские настройки и инструменты — только админу
    assert _guest_feature("/api/settings/news") is None and _guest_feature("/api/tools") is None
    for path in ("/api/overview", "/api/finance", "/api/power", "/api/chat", "/api/backup", "/api/sites", "/api/docker"):
        assert _guest_feature(path) is None


def test_guest_gets_only_allowed_features():
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(Path(tmp) / "s.json")
        store.load()
        access = AccessService(store, ADMIN)
        asyncio.run(access.register(7, "Вася", None))
        ctx = SimpleNamespace(
            settings=SimpleNamespace(bot_token=SimpleNamespace(get_secret_value=lambda: TOKEN), admin_id=ADMIN), access=access
        )
        assert call(ctx, "/api/notes", 7)[0] == 401  # заявка ещё не принята
        asyncio.run(access.set_status(7, APPROVED))
        assert call(ctx, "/api/notes", 7)[0] == 200
        assert call(ctx, "/api/me", 7)[0] == 200
        assert call(ctx, "/api/overview", 7)[0] == 403  # серверные разделы — только админу
        assert call(ctx, "/api/finance", 7)[0] == 403
        asyncio.run(access.toggle(7, "notes"))  # выключили заметки
        assert call(ctx, "/api/notes", 7)[0] == 403
        assert call(ctx, "/api/notes", 555)[0] == 401  # незнакомый
        assert call(ctx, "/api/overview", ADMIN)[0] == 200  # админ — всё
