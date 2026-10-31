"""10.0: внешние модули — манифест, права, изоляция ошибок."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from homebot import plugins

EXAMPLE = Path(__file__).resolve().parent.parent / "extras" / "modules" / "uptime"


def _mod(root: Path, mid: str, code: str, **manifest) -> Path:
    folder = root / mid
    folder.mkdir(parents=True)
    data = {"id": mid, "name": mid, "version": "1", "api": 1, "commands": [{"command": mid}], **manifest}
    (folder / "module.json").write_text(json.dumps(data))
    (folder / "module.py").write_text(code)
    return folder


def test_manifest_rules():
    with pytest.raises(plugins.ModuleError):
        plugins.parse_manifest({"id": "x1", "api": 2}, "x1")
    with pytest.raises(plugins.ModuleError):
        plugins.parse_manifest({"id": "ok", "api": 1, "permissions": ["root"]}, "ok")
    with pytest.raises(plugins.ModuleError):
        plugins.parse_manifest({"id": "ok", "api": 1}, "other")
    m = plugins.parse_manifest({"id": "ok", "api": 1, "permissions": ["network", "network"]}, "ok")
    assert m.permissions == ("network",)


def test_load_run_and_isolation(tmp_path):
    _mod(tmp_path, "hello", "def setup(uni):\n    async def h(a):\n        return 'hi ' + a\n    uni.command('hello', h)\n")
    _mod(tmp_path, "broken", "raise RuntimeError('boom')\n")
    _mod(tmp_path, "greedy", "def setup(uni):\n    uni.http\n")  # нет права network
    _mod(tmp_path, "flaky", "def setup(uni):\n    async def h(a):\n        raise ValueError(a)\n    uni.command('flaky', h)\n")
    _mod(tmp_path, "clash", "def setup(uni):\n    pass\n", commands=[{"command": "status"}])
    _mod(tmp_path, "off", "raise SystemExit\n")
    _mod(tmp_path, "quitter", "raise SystemExit\n")
    reg = plugins.Registry(tmp_path)
    found = reg.discover(["hello", "broken", "greedy", "flaky", "clash", "quitter"])
    assert {p.manifest.id for p in found} == {"hello", "broken", "greedy", "flaky", "clash", "off", "quitter"}
    results = {p.manifest.id: reg.load(p, {"status"}) for p in found}
    assert results == {"hello": True, "broken": False, "greedy": False, "flaky": True, "clash": False, "off": False, "quitter": False}
    assert "boom" in reg.plugins["broken"].error
    assert "network" in reg.plugins["greedy"].error
    assert "status" in reg.plugins["clash"].error
    assert reg.plugins["off"].error == ""  # выключенный модуль даже не импортируется

    assert asyncio.run(reg.run_command("hello", "Patrik")) == "hi Patrik"
    assert asyncio.run(reg.run_command("nope", "")) is None
    for _ in range(plugins.MAX_FAILS - 1):
        assert "ошибся" in asyncio.run(reg.run_command("flaky", "x"))
    assert "отключён" in asyncio.run(reg.run_command("flaky", "x"))
    assert asyncio.run(reg.run_command("flaky", "x")) is None
    assert reg.plugins["hello"].as_json()["loaded"] is True


def test_example_module_loads(tmp_path):
    shutil.copytree(EXAMPLE, tmp_path / "uptime")
    reg = plugins.Registry(tmp_path)
    (plugin,) = reg.discover(["uptime"])
    assert reg.load(plugin, set(), http=object())
    assert "uptime" in reg.commands and plugin.jobs[0][0] == 300
    reply = asyncio.run(reg.run_command("uptime", ""))
    assert "Сайтов нет" in reply


class _Store:
    def __init__(self):
        self.data = {}

    def get(self, k, d=None):
        return self.data.get(k, d)

    async def set(self, k, v):
        self.data[k] = v


def test_enabled_store():
    s = _Store()
    assert asyncio.run(plugins.set_enabled(s, "a", True)) == ["a"]
    assert asyncio.run(plugins.set_enabled(s, "b", True)) == ["a", "b"]
    assert asyncio.run(plugins.set_enabled(s, "a", False)) == ["b"]
    assert plugins.enabled_ids(s) == ["b"]
