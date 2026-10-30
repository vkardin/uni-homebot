"""5.13: 🛠 ремонт сервера — выключен по умолчанию, PIN, белый список, журнал."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from homebot.config import Settings
from homebot.services import repair as repair_mod
from homebot.services.power import ActionResult
from homebot.services.repair import Repair


class FakePower:
    restartable = ("ollama",)
    power_enabled = False

    def __init__(self):
        self.calls = []

    async def restart(self, unit):
        self.calls.append(unit)
        return ActionResult(True, "перезапущена")

    async def reboot(self):
        self.calls.append("reboot")
        return ActionResult(True, "ok")


def _repair(tmp_path, **env):
    settings = Settings(_env_file=None, bot_token="1:x", admin_id=1, state_dir=tmp_path, **env)  # type: ignore[call-arg]
    power = FakePower()
    return Repair(settings, power, SimpleNamespace(repair=lambda: 2)), power  # type: ignore[arg-type]


def test_off_by_default_and_needs_pin(tmp_path):
    r, _ = _repair(tmp_path)
    assert not r.enabled
    assert asyncio.run(r.run("tasks", "1234", 1)) == (False, "модуль выключен")
    assert not _repair(tmp_path, repair_enabled=True)[0].enabled  # без PIN не включается
    assert not _repair(tmp_path, repair_enabled=True, repair_pin="12")[0].enabled  # слишком короткий


def test_whitelist_and_journal(tmp_path):
    r, power = _repair(tmp_path, repair_enabled=True, repair_pin="2468")
    assert [a.id for a in r.actions()] == ["bot", "tasks", "svc:ollama"]  # без POWER_ENABLED перезагрузки нет
    assert asyncio.run(r.run("svc:ollama", "2468", 1)) == (True, "перезапущена")
    assert asyncio.run(r.run("reboot", "2468", 1)) == (False, "такого действия нет")
    assert asyncio.run(r.run("svc:sshd", "2468", 1))[0] is False
    assert power.calls == ["ollama"]
    assert asyncio.run(r.run("tasks", "2468", 1)) == (True, "перезапущено задач: 2")
    assert [h["action"] for h in r.history()] == ["tasks", "svc:ollama"]
    assert oct((tmp_path / repair_mod.LOG_NAME).stat().st_mode & 0o777) == "0o600"


def test_pin_lockout(tmp_path):
    r, _ = _repair(tmp_path, repair_enabled=True, repair_pin="2468")
    for _ in range(repair_mod.MAX_FAILS - 1):
        assert r.check_pin("0000", 1, now=100) == "wrong"
    assert r.check_pin("0000", 1, now=100) == "locked"
    assert r.check_pin("2468", 1, now=101) == "locked"  # даже верный PIN — пока закрыто
    assert r.check_pin("2468", 1, now=100 + repair_mod.LOCK_SEC + 1) == "ok"
    assert sum(1 for h in r.history(50) if h["action"] == "pin") == repair_mod.MAX_FAILS
