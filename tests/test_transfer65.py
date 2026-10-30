"""6.5: перенос настроек одним файлом и settings.json в бэкапе."""

from __future__ import annotations

import asyncio
import json

import pytest

from homebot.services import overrides, transfer
from homebot.services.state import StateStore


def _store(path):
    store = StateStore(path / "state.json")
    store.load()
    return store


def test_roundtrip(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.mkdir()
    dst.mkdir()
    s1 = _store(src)
    asyncio.run(s1.set("launcher_links", [{"title": "NAS", "url": "https://nas.test", "icon": "🔗"}]))
    asyncio.run(s1.set("modules_off", ["news"]))
    asyncio.run(s1.set("access", {"secret": 1}))  # люди не переносятся
    overrides.save(src, {"timezone": "Europe/Berlin"})
    raw = transfer.export(src, s1)
    assert b"secret" not in raw
    parsed = transfer.parse(raw)
    s2 = _store(dst)
    done = asyncio.run(transfer.apply(parsed, dst, s2))
    assert done and s2.get("modules_off") == ["news"] and s2.get("launcher_links")[0]["title"] == "NAS"
    assert overrides.load(dst) == {"timezone": "Europe/Berlin"}
    assert transfer.is_settings_file("UNI-settings (1).json") and not transfer.is_settings_file("x.json")


def test_bad_files():
    for raw in (b"not json", b"[]", json.dumps({"format": "other"}).encode(), b"x" * (transfer.MAX_SIZE + 1)):
        with pytest.raises(transfer.TransferError):
            transfer.parse(raw)
    parsed = transfer.parse(json.dumps({"format": "uni-settings", "settings": {"timezone": "Nope/Nope", "bot_token": "x"},
                                        "state": {"access": 1, "prefs.news": {}}}).encode())  # fmt: skip
    assert parsed["settings"] == {} and parsed["state"] == {"prefs.news": {}}


def test_backup_keeps_panel_settings(tmp_path):
    from homebot.services import backup
    from homebot.services.db import Database

    db = Database(tmp_path / "homebot.db")
    db.open()
    store = _store(tmp_path)
    overrides.save(tmp_path, {"currency": "€"})
    item = asyncio.run(backup.create_backup(tmp_path, db))
    overrides.save(tmp_path, {})
    done = asyncio.run(backup.restore_backup(item.path, tmp_path, db, store))
    assert "настройки панели" in done and overrides.load(tmp_path) == {"currency": "€"}
    db.close()
