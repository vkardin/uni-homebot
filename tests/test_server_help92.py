"""9.2: помощник о сервере собирает факты и отвечает только по ним."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

from homebot.services import server_help


class _Sys:
    async def snapshot(self):
        disk = NS(mountpoint="/", percent=93.0)
        return NS(
            cpu_percent=12,
            cpu_threads=4,
            load_avg=(0.5, 0.4, 0.3),
            ram_used=3 * 1024**3,
            ram_total=8 * 1024**3,
            ram_percent=37.5,
            swap_percent=0,
            disks=(disk,),
            temperature=NS(current=55),
            uptime_sec=7200,
        )

    async def top_processes(self, limit):
        return [NS(name="jellyfin", rss=1024**3)]


class _Units:
    async def status_all(self):
        return [NS(name="nginx.service", active_state="failed"), NS(name="ssh.service", active_state="active")]


class _Docker:
    async def containers_raw(self):
        return [{"Names": ["/qbit"], "State": "exited"}, {"Names": ["/jf"], "State": "running"}]


class _Ollama:
    def __init__(self):
        self.seen = None

    async def ask(self, system, text):
        self.seen = (system, text)
        return "Диск / почти заполнен."


def test_facts_and_answer():
    facts = asyncio.run(server_help.facts(_Sys(), _Units(), NS(docker=_Docker()), NS(broken=lambda: [("news", "x")])))
    assert "Диск /: 93%" in facts
    assert "nginx.service" in facts and "ssh.service" not in facts
    assert "qbit" in facts and "jellyfin" in facts
    assert "news" in facts
    o = _Ollama()
    assert asyncio.run(server_help.answer(o, "что съело диск?", facts)) == "Диск / почти заполнен."
    assert "Диск /: 93%" in o.seen[0] and "не выполняй" in o.seen[0]


def test_facts_survive_failures():
    class Bad:
        async def snapshot(self):
            raise OSError

        async def top_processes(self, limit):
            raise OSError

        async def status_all(self):
            raise OSError

    facts = asyncio.run(server_help.facts(Bad(), Bad(), NS(docker=None)))
    assert facts == ""
