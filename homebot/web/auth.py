"""Проверка подписи Telegram Mini App (initData) — https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AGE_SEC = 24 * 3600


def verify_init_data(init_data: str, bot_token: str, admin_id: int, *, now: float | None = None, max_age: int = MAX_AGE_SEC) -> bool:
    """True, только если данные подписаны токеном этого бота, свежие и принадлежат администратору."""
    user = verify_init_user(init_data, bot_token, now=now, max_age=max_age)
    return user is not None and user.get("id") == admin_id


def verify_init_user(init_data: str, bot_token: str, *, now: float | None = None, max_age: int = MAX_AGE_SEC) -> dict | None:
    """Пользователь из initData, если подпись этого бота верна и данные свежие; иначе None. Права проверяет вызывающий."""
    if not init_data or len(init_data) > 8192:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received = pairs.pop("hash", "")
    if not received:
        return None
    check = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        issued = int(pairs.get("auth_date", "0"))
        user = json.loads(pairs.get("user", "{}"))
    except ValueError:
        return None
    current = time.time() if now is None else now
    if issued <= 0 or current - issued > max_age or issued - current > 300:
        return None
    return user if isinstance(user, dict) and isinstance(user.get("id"), int) else None


def sign_for_tests(fields: dict[str, str], bot_token: str) -> str:
    """Собирает корректно подписанный initData (нужно только тестам)."""
    from urllib.parse import urlencode

    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})
