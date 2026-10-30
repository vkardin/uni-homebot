"""Тексты раздела «💾 Бэкап». Без aiogram."""

from __future__ import annotations

import html
from datetime import datetime, tzinfo
from typing import Any

from homebot.services.backup import BackupFile
from homebot.ui.style import DIV

esc = html.escape


def size_text(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} МБ"
    return f"{max(1, size // 1024)} КБ"


def when(ts: int, tz: tzinfo | None) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%d.%m %H:%M")


def render_backup_home(
    items: list[BackupFile], status: dict[str, Any], hour: int, keep: int, tz: tzinfo | None, extra_count: int
) -> str:
    lines = ["💾 <b>Бэкап</b>", DIV, f"Автоматически: каждый день в {hour:02d}:00, храню последние {keep}."]
    lines.append(
        "В копии: база (заметки, напоминания, история) и состояние бота"
        + (f" + файлов из BACKUP_PATHS: {extra_count}." if extra_count else ".")
    )
    lines.append("<i>.env с токенами не копируется.</i>")
    if status.get("ok") is False:
        lines += ["", f"⚠️ Последняя попытка не удалась: {esc(str(status.get('error', '')))}"]
    lines.append("")
    if not items:
        lines.append("Копий пока нет — нажмите «Сохранить сейчас».")
    else:
        lines.append("Копии:")
        lines += [f"• {when(item.ts, tz)} · {size_text(item.size)}" for item in items]
    return "\n".join(lines)


def render_restore_confirm(item: BackupFile, tz: tzinfo | None) -> str:
    return (
        f"↩️ <b>Восстановить копию от {when(item.ts, tz)}?</b>\n{DIV}\n"
        "База и состояние бота будут заменены содержимым копии: заметки, напоминания и настройки вернутся к тому моменту. "
        "Перед этим я автоматически сохраню текущее состояние отдельной копией."
    )
