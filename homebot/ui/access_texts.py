"""Тексты раздела «👥 Доступ» и экранов гостя."""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import datetime, tzinfo

from homebot.services.access import APPROVED, BLOCKED, FEATURES, PENDING, STATUS_ICON, Person
from homebot.ui.style import DIV

STATUS_NAME = {PENDING: "ждёт решения", APPROVED: "принят", BLOCKED: "заблокирован"}

GUEST_PENDING = "👋 Привет! Это личный бот-помощник.\nЯ передал хозяину твою заявку. Как только он её одобрит, я напишу сюда 🙂"
GUEST_WELCOME = "🤖 <b>UNI</b>\n" + DIV + "\nДоступ открыт! Вот что тебе можно:"
GUEST_NOTHING = "Пока хозяин не включил тебе ни одной функции. Попроси его — и они появятся."
GUEST_DENIED = "🔒 Эта функция тебе не включена. Её может открыть хозяин бота."
GUEST_LIMIT = "Достигнут лимит ({limit}). Удали что-нибудь лишнее."
GUEST_UNKNOWN = "🤔 Я не понял. Открой меню: /menu"


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def when(ts: int, tz: tzinfo | None) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m %H:%M") if ts else "—"


def render_request(person: Person) -> str:
    return (
        "🔔 <b>Новая заявка на доступ</b>\n"
        f"{DIV}\n"
        f"👤 {esc(person.label)}\n"
        f"🆔 <code>{person.id}</code>\n\n"
        "Принять — человек получит доступ к разрешённым по умолчанию функциям (заметки, напоминания, курс, новости). "
        "Права можно поменять в любой момент."
    )


def render_people(people: Sequence[Person], tz: tzinfo | None) -> str:
    lines = ["👥 <b>Доступ</b>", DIV]
    if not people:
        lines.append("Пока никто не просился. Дай друзьям ссылку на бота — их заявки придут сюда.")
        return "\n".join(lines)
    pending = sum(1 for p in people if p.status == PENDING)
    if pending:
        lines.append(f"🕓 Ждут решения: <b>{pending}</b>")
    for p in people:
        lines.append(f"{STATUS_ICON[p.status]} {esc(p.label)} · <i>{STATUS_NAME[p.status]}</i>")
    lines += ["", "Нажми на человека, чтобы принять, заблокировать или выбрать, чем он может пользоваться."]
    return "\n".join(lines)


def render_person(person: Person, tz: tzinfo | None) -> str:
    perms = ", ".join(FEATURES[p] for p in FEATURES if p in person.perms) or "ничего"
    return (
        f"👤 <b>{esc(person.label)}</b>\n{DIV}\n"
        f"🆔 <code>{person.id}</code>\n"
        f"Статус: {STATUS_ICON[person.status]} <b>{STATUS_NAME[person.status]}</b>\n"
        f"Заявка: {when(person.created, tz)} · заходил: {when(person.seen, tz)}\n"
        f"Доступно: {perms}\n\n"
        "Кнопки ниже включают и выключают функции. Права вступают в силу сразу."
    )


def render_remove(person: Person) -> str:
    return (
        f"❓ <b>Удалить {esc(person.label)}?</b>\n"
        "Заметки и напоминания человека останутся в базе, но он снова попадёт в заявки, если напишет боту."
    )
