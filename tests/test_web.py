from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from homebot.web.auth import sign_for_tests, verify_init_data
from homebot.web.serialize import plain

TOKEN = "123456:TEST-token"


def _fields(user_id=42, age=0):
    return {"auth_date": str(int(time.time()) - age), "query_id": "AAH", "user": f'{{"id":{user_id},"first_name":"P"}}'}


def test_init_data_accepts_only_fresh_admin_signed_by_our_bot():
    good = sign_for_tests(_fields(), TOKEN)
    assert verify_init_data(good, TOKEN, 42)
    assert not verify_init_data(good, TOKEN, 43)  # не админ
    assert not verify_init_data(good, "999:OTHER", 42)  # подпись чужого бота
    assert not verify_init_data(sign_for_tests(_fields(age=3 * 86400), TOKEN), TOKEN, 42)  # протухло
    assert not verify_init_data(sign_for_tests(_fields(age=-3600), TOKEN), TOKEN, 42)  # «из будущего»
    tampered = good.replace("%22id%22%3A42", "%22id%22%3A43")
    assert tampered != good and not verify_init_data(tampered, TOKEN, 43)
    for junk in ("", "hash=abc", "a=b", "x" * 9000, "auth_date=1&hash=00"):
        assert not verify_init_data(junk, TOKEN, 42)


def test_plain_serializes_service_objects():
    class Color(Enum):
        RED = "red"

    @dataclass(frozen=True, slots=True)
    class Inner:
        when: datetime
        path: Path

    @dataclass(frozen=True, slots=True)
    class Outer:
        inner: Inner
        items: tuple[int, ...]
        color: Color
        bad: float

    value = Outer(Inner(datetime.fromtimestamp(1_800_000_000), Path("/x")), (1, 2), Color.RED, float("nan"))
    assert plain(value) == {"inner": {"when": 1_800_000_000, "path": "/x"}, "items": [1, 2], "color": "red", "bad": None}
    assert plain({"a": {1, 2}}) == {"a": [1, 2]} or plain({"a": {1, 2}}) == {"a": [2, 1]}
    assert plain(object()).startswith("<object")
