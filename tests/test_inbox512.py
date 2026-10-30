"""5.12: 🔔 центр уведомлений."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from homebot.services import inbox as inbox_mod
from homebot.services.inbox import Inbox
from homebot.services.quiet import Importance, set_importance


def test_add_read_persist(tmp_path):
    box = Inbox(tmp_path)
    box.add("<b>Фильм</b> скачан &amp; готов", now=10)
    box.add("  ", now=11)
    box.add("Диск горячий", critical=True, now=20)
    assert box.unread() == 2
    assert [i["text"] for i in box.items()] == ["Диск горячий", "Фильм скачан & готов"]
    box.mark_read(now=15)
    again = Inbox(tmp_path)
    assert again.unread() == 1 and again.items()[0]["critical"] is True
    again.clear()
    assert Inbox(tmp_path).items() == []


def test_limit(tmp_path):
    box = Inbox(tmp_path)
    for n in range(inbox_mod.LIMIT + 5):
        box.add(f"m{n}", now=n)
    assert len(box.items()) == inbox_mod.LIMIT and box.items()[0]["text"] == f"m{inbox_mod.LIMIT + 4}"


def test_broken_file(tmp_path):
    (tmp_path / inbox_mod.FILE_NAME).write_text("[1,2")
    assert Inbox(tmp_path).items() == []


def test_middleware_copies_only_background(tmp_path):
    try:
        from aiogram.methods import SendMessage

        from homebot.utils.inbox_middleware import InboxMiddleware
    except ImportError:
        return
    box = Inbox(tmp_path)
    mw = InboxMiddleware(box, 42)

    async def make_request(bot, method):
        return "ok"

    async def run(level, chat, text):
        set_importance(level)
        return await mw(make_request, SimpleNamespace(), SendMessage(chat_id=chat, text=text))  # type: ignore[arg-type]

    assert asyncio.run(run(Importance.INTERACTIVE, 42, "ответ")) == "ok"
    asyncio.run(run(Importance.NORMAL, 42, "новости"))
    asyncio.run(run(Importance.CRITICAL, 7, "гостю"))
    assert [i["text"] for i in box.items()] == ["новости"]
