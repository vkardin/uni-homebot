"""10.0: данные установки 6.0 читаются без миграции всеми новыми частями (обновление с любой 6.x)."""

from __future__ import annotations

import asyncio
import json
import sqlite3

from homebot import plugins
from homebot.services import launcher, modules, overrides, reports, rules, setup, transfer
from homebot.services.db import Database, MetricRow
from homebot.services.state import StateStore

# state.json, как его оставляет 6.0.0
STATE_60 = {
    "app.version": "6.0.0",
    "modules_off": ["movies", "p2p"],
    "launcher_links": [{"title": "Jellyfin", "url": "http://192.168.1.5:8096", "icon": "film"}],
    "prefs.news": {"feeds": ["https://example.com/rss"]},
    "status.message_id": 42,
}


def test_state_60_reads_everywhere(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps(STATE_60))
    store = StateStore(tmp_path / "state.json")
    store.load()
    assert {"movies", "p2p"} <= modules.off(store)
    assert launcher.links(store)[0]["url"] == "http://192.168.1.5:8096"
    assert rules.load(store) == []
    assert plugins.enabled_ids(store) == []
    assert setup.hidden(store) is False
    assert overrides.load(tmp_path) == {}
    data = json.loads(transfer.export(tmp_path, store))
    assert data["state"]["modules_off"] == ["movies", "p2p"]


def test_db_60_gets_hourly_table(tmp_path):
    path = tmp_path / "homebot.db"
    con = sqlite3.connect(path)  # схема метрик 6.0, без metrics_hourly
    con.execute(
        "CREATE TABLE metrics (ts INTEGER PRIMARY KEY, cpu REAL NOT NULL, ram REAL NOT NULL, swap REAL NOT NULL, temp REAL, disk REAL)"
    )
    con.execute("INSERT INTO metrics VALUES (3600, 20, 30, 0, NULL, 40)")
    con.commit()
    con.close()
    db = Database(path)
    db.open()
    asyncio.run(db.add_metric(MetricRow(ts=3700, cpu=40, ram=50, swap=0, temp=None, disk=41)))
    assert asyncio.run(db.rollup_hours(7200)) == 1
    rep = asyncio.run(reports.make(db, "week", now=7200))
    assert rep is not None and rep.cpu_avg == 30 and rep.cpu_max == 40
    db.close()
