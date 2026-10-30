"""Тексты раздела «⬆️ Обновление». Без aiogram — проверяются обычными тестами."""

from __future__ import annotations

import html
from typing import Any

from homebot.services.selfupdate import ArchiveInfo, compare_versions
from homebot.ui.style import DIV

esc = html.escape

NOT_INSTALLED = (
    "⚠️ <b>Обновлятор не установлен.</b> Выполните один раз обычную установку "
    "(<code>sudo bash homebot/deploy/update.sh --test</code>) — после неё архивы можно присылать в чат."
)
NOT_ARCHIVE = "Это не архив обновления UNI. Нужен архив <code>homebot-X.Y.Z.tar.gz</code> (папка <code>homebot/</code> внутри)."


def render_update_home(version: str, *, ready: bool, previous: str | None) -> str:
    lines = ["⬆️ <b>Обновление</b>", DIV, f"Сейчас: <b>v{esc(version)}</b>"]
    if previous:
        lines.append(f"Прежняя (для отката): v{esc(previous)}")
    lines.append("")
    if ready:
        lines += [
            "Пришлите сюда архив <code>homebot-X.Y.Z.tar.gz</code> (любое имя на .tar.gz) — проверю, покажу, "
            "что нового, и по кнопке установлю.",
            "Перед заменой кода прогоняются тесты, а если новая версия не запустится — вернусь на прежнюю сам.",
        ]
    else:
        lines.append(NOT_INSTALLED)
    return "\n".join(lines)


def render_archive(info: ArchiveInfo, current: str) -> str:
    relation = compare_versions(info.version, current)
    lines = [f"📦 <b>Архив обновления</b>: v{esc(current)} → <b>v{esc(info.version)}</b>", DIV]
    if relation == 0:
        lines.append("⚠️ Это та же версия, что установлена сейчас.")
    elif relation < 0:
        lines.append("⚠️ Эта версия старее установленной.")
    lines.append(f"Файлов: {info.files} · {info.size / 1024:.0f} КБ · зависимости: {'изменились' if info.deps_changed else 'те же'}")
    if info.whats_new:
        lines += ["", "<b>Что нового</b>"] + [f"• {esc(item)}" for item in info.whats_new[:6]]
    if info.system_changed:
        shown = ", ".join(info.system_changed[:5]) + ("…" if len(info.system_changed) > 5 else "")
        lines += [
            "",
            f"🔧 В этой версии изменились системные файлы (<code>{esc(shown)}</code>), "
            "поэтому из чата её поставить нельзя. Установите обычным способом: "
            "<code>sudo bash homebot/deploy/update.sh --test</code>.",
        ]
    return "\n".join(lines)


def render_started(version: str) -> str:
    return (
        f"⏳ <b>Ставлю v{esc(version)}…</b>\nСначала тесты, потом замена кода и перезапуск — обычно 1–2 минуты. "
        "Бот ненадолго замолчит; о результате напишу сам."
    )


def render_rollback_confirm(current: str, previous: str) -> str:
    return f"↩️ Вернуть <b>v{esc(previous)}</b> вместо v{esc(current)}?\nБот перезапустится."


STALE = "Подтверждение устарело — пришлите архив заново."
NOT_FOUND = "Архив не найден — пришлите файл заново."
WATCHDOG = (
    "⚠️ Обновлятор так и не взял задачу. Проверьте на сервере: "
    "<code>systemctl status homebot-update.path</code> и <code>journalctl -u homebot-update -n 30</code>."
)


def render_result(result: dict[str, Any]) -> str:
    to = esc(str(result.get("to_version") or "?"))
    before = esc(str(result.get("from_version") or "?"))
    stage = result.get("stage")
    if result.get("ok") and stage == "rollback":
        return f"↩️ <b>Откат выполнен</b>: v{before} → v{to}"
    if result.get("ok"):
        extra = []
        if result.get("tests"):
            extra.append(f"тесты: {esc(str(result['tests']))}")
        if result.get("deps"):
            extra.append("зависимости обновлены")
        added = result.get("env_added")
        if isinstance(added, list) and added:
            extra.append("новые параметры .env: " + esc(", ".join(str(a) for a in added[:8])))
        tail = ("\n" + " · ".join(extra)) if extra else ""
        return f"✅ <b>Обновлено</b>: v{before} → v{to}{tail}"
    reason = esc(str(result.get("message") or "причина неизвестна"))
    titles = {
        "archive": "архив не прошёл проверку",
        "system": "нужна обычная установка",
        "deps": "не поставились зависимости",
        "tests": "тесты новой версии не прошли — код не менялся",
        "start": "новая версия не запустилась",
        "rollback": "откат не удался",
        "crash": "сбой обновлятора",
    }
    title = titles.get(str(stage), "обновление не удалось")
    back = "\n↩️ Вернул прежнюю версию, бот работает как раньше." if result.get("rolled_back") else ""
    return f"❌ <b>Обновление до v{to} не выполнено</b>: {title}\n{reason}{back}"
