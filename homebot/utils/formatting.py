"""Чистые функции форматирования — без зависимостей от aiogram."""

from __future__ import annotations

_BYTE_UNITS = ("Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ")


def fmt_bytes(value: float) -> str:
    """1536 -> '1.5 КБ'."""
    size = float(value)
    index = 0
    while abs(size) >= 1024 and index < len(_BYTE_UNITS) - 1:
        size /= 1024
        index += 1
    if index == 0:
        return f"{size:.0f} {_BYTE_UNITS[0]}"
    return f"{size:.1f} {_BYTE_UNITS[index]}"


def fmt_duration(seconds: float) -> str:
    """93784 -> '1д 2ч 3м'. Для коротких интервалов опускает старшие единицы."""
    total = max(0, int(seconds))
    days, rest = divmod(total, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes = rest // 60
    parts: list[str] = []
    if days:
        parts.append(f"{days}д")
    if days or hours:
        parts.append(f"{hours}ч")
    parts.append(f"{minutes}м")
    return " ".join(parts)


def bar(percent: float, width: int = 10) -> str:
    """Текстовая шкала: 40 -> '▰▰▰▰▱▱▱▱▱▱'."""
    clamped = min(100.0, max(0.0, percent))
    filled = round(clamped / 100 * width)
    return "▰" * filled + "▱" * (width - filled)


def truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Режет длинный текст на части под лимит Telegram (4096), по возможности по строкам."""
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    chunks.append(text)
    return chunks
