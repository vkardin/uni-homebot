"""9.0: сценарии «если, то»."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from homebot.services import rules


class Store(dict):
    def get(self, k, d=None):
        return super().get(k, d)

    async def set(self, k, v):
        self[k] = v


def test_make_and_describe():
    r = rules.make({"when": "disk", "value": "90,5", "then": "notify"})
    assert r.value == 90.5 and "диск выше 90.5" in rules.describe(r)
    r = rules.make({"when": "container_down", "target": "jellyfin", "then": "restart_container"})
    assert r.then_target == "jellyfin"
    for bad in [{"when": "x"}, {"when": "cpu", "value": "a"}, {"when": "service_failed", "target": "rm -rf /"},
                {"when": "cpu", "value": 1, "then": "restart_service", "then_target": ""}]:  # fmt: skip
        with pytest.raises(ValueError):
            rules.make(bad)


def test_engine_edge_and_cooldown():
    r = rules.make({"id": "a", "when": "cpu", "value": 80, "cooldown": 10})
    e = rules.Engine()
    assert e.due([r], {"cpu": 90}, now=0 + 1000) == [r]
    assert e.due([r], {"cpu": 95}, now=1060) == []  # всё ещё верно — не повторяем
    assert e.due([r], {"cpu": 10}, now=1120) == []
    assert e.due([r], {"cpu": 90}, now=1180) == []  # снова верно, но кулдаун 10 мин
    assert e.due([r], {"cpu": 10}, now=1700) == []
    assert e.due([r], {"cpu": 90}, now=1800) == [r]
    assert e.due([r], {}, now=5000) == []  # нет данных — не трогаем


def test_job_restarts_container():
    store = Store()
    asyncio.run(rules.save(store, [rules.make({"id": "j", "when": "container_down", "target": "jf", "then": "restart_container"})]))
    restarted, sent = [], []

    class Docker:
        async def containers_raw(self):
            return [{"State": "exited", "Names": ["/jf"]}]

        async def restart(self, name):
            restarted.append(name)
            return True, "перезапущен"

    class Bot:
        async def send_message(self, chat, text):
            sent.append(text)

    from homebot.jobs.rules_watch import RulesJob

    job = RulesJob(
        bot=Bot(), admin_id=1, store=store, system=None, units=None, apps=SimpleNamespace(docker=Docker(), p2p=None), power=None
    )
    assert len(asyncio.run(job.tick())) == 1 and restarted == ["jf"] and "перезапущен" in sent[0]
    assert asyncio.run(job.tick()) == []
