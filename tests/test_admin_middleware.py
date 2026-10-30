import asyncio
from types import SimpleNamespace

from aiogram.enums import ChatType

from homebot.middlewares import AdminOnlyMiddleware

ADMIN = 1001


def user(user_id):
    return SimpleNamespace(id=user_id)


def chat(kind=ChatType.PRIVATE):
    return SimpleNamespace(type=kind)


class Probe:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, event, data):
        self.calls += 1
        return "handled"


def run(event, data, *, middleware=None):
    middleware = middleware or AdminOnlyMiddleware(ADMIN)
    probe = Probe()
    result = asyncio.run(middleware(probe, event, data))
    return result, probe.calls


def update(**fields):
    return SimpleNamespace(update_id=1, **fields)


def test_admin_in_private_chat_passes():
    result, calls = run(update(), {"event_from_user": user(ADMIN), "event_chat": chat()})
    assert (result, calls) == ("handled", 1)


def test_stranger_is_dropped_silently():
    result, calls = run(update(), {"event_from_user": user(999), "event_chat": chat()})
    assert (result, calls) == (None, 0)


def test_unknown_author_is_dropped_fail_closed():
    # Нет event_from_user и в самом Update тоже нет автора (например, channel_post / poll).
    result, calls = run(update(), {})
    assert (result, calls) == (None, 0)


def test_author_is_resolved_from_update_when_context_missing():
    event = update(message=SimpleNamespace(from_user=user(ADMIN), chat=chat()))
    assert run(event, {}) == ("handled", 1)
    stranger = update(message=SimpleNamespace(from_user=user(5), chat=chat()))
    assert run(stranger, {}) == (None, 0)


def test_callback_query_chat_is_taken_from_its_message():
    cb = SimpleNamespace(from_user=user(ADMIN), message=SimpleNamespace(chat=chat()))
    assert run(update(callback_query=cb), {}) == ("handled", 1)
    group_cb = SimpleNamespace(from_user=user(ADMIN), message=SimpleNamespace(chat=chat(ChatType.SUPERGROUP)))
    assert run(update(callback_query=group_cb), {}) == (None, 0)


def test_admin_in_group_is_ignored_so_metrics_do_not_leak():
    for kind in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
        result, calls = run(update(), {"event_from_user": user(ADMIN), "event_chat": chat(kind)})
        assert (result, calls) == (None, 0), kind


def test_admin_with_unknown_chat_is_dropped_fail_closed():
    assert run(update(), {"event_from_user": user(ADMIN)}) == (None, 0)


def test_blocked_attempts_are_logged_rarely():
    middleware = AdminOnlyMiddleware(ADMIN, log_interval=3600)
    with_logs = []

    import logging

    class Capture(logging.Handler):
        def emit(self, record):
            with_logs.append(record.getMessage())

    logger = logging.getLogger("homebot.middlewares.admin_only")
    handler = Capture()
    logger.addHandler(handler)
    try:
        for _ in range(50):  # флуд с одного ID не должен забивать журнал
            run(update(), {"event_from_user": user(777), "event_chat": chat()}, middleware=middleware)
    finally:
        logger.removeHandler(handler)
    assert len(with_logs) == 1 and "777" in with_logs[0]
