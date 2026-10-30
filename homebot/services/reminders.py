"""Разбор времени напоминаний на естественном языке (русский, частично украинский).

Поддерживается:
  через 15 минут / через 2 часа / через час / через полчаса / через 3 дня / через неделю
  сегодня 18:00 / завтра (в 9:00) / послезавтра 10:30
  в 18:30            — ближайшее такое время
  15.10 (9:00) / 15.10.2026 в 9:00
  в понедельник 10:00 / пятница

Повторяющиеся:
  каждый день 8:00 … / по будням 9:00 … / каждый понедельник 9:00 …
  каждый месяц 15 числа 10:00 … / каждые 2 часа …
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

_FLAGS = re.IGNORECASE | re.DOTALL
_TIME = r"(?:\s+(?:в\s+|о\s+|at\s+)?(?P<h>\d{1,2})[:.](?P<m>\d{2}))?"
DEFAULT_HOUR = 9

_RELATIVE_RE = re.compile(r"^через\s+(?:(?P<n>\d+)\s*)?(?P<unit>[a-zа-яёіїєґ]+)\.?\s+(?P<rest>.+)$", _FLAGS)
_DAY_RE = re.compile(rf"^(?P<day>сегодня|сьогодні|завтра|послезавтра|післязавтра){_TIME}\s+(?P<rest>.+)$", _FLAGS)
_AT_RE = re.compile(r"^(?:в|о|at)\s+(?P<h>\d{1,2})[:.](?P<m>\d{2})\s+(?P<rest>.+)$", _FLAGS)
_DATE_RE = re.compile(rf"^(?P<d>\d{{1,2}})\.(?P<mo>\d{{1,2}})(?:\.(?P<y>\d{{4}}))?{_TIME}\s+(?P<rest>.+)$", _FLAGS)

_WEEKDAYS = {
    "понедельник": 0, "пн": 0, "понеділок": 0,
    "вторник": 1, "вт": 1, "вівторок": 1,
    "среду": 2, "среда": 2, "ср": 2, "середу": 2, "середа": 2,
    "четверг": 3, "чт": 3, "четвер": 3,
    "пятницу": 4, "пятница": 4, "пт": 4, "пʼятницю": 4, "п'ятницю": 4,
    "субботу": 5, "суббота": 5, "сб": 5, "суботу": 5,
    "воскресенье": 6, "вс": 6, "неділю": 6, "неділя": 6, "нд": 6,
}  # fmt: skip
_WEEKDAY_NAMES = "|".join(sorted(map(re.escape, _WEEKDAYS), key=len, reverse=True))
_WEEKDAY_RE = re.compile(rf"^(?:(?:в|во|у)\s+)?(?P<wd>{_WEEKDAY_NAMES}){_TIME}\s+(?P<rest>.+)$", _FLAGS)


@dataclass(frozen=True, slots=True)
class ParsedReminder:
    due: datetime
    text: str
    rule: str = ""  # пусто — разовое; иначе правило повтора (см. next_due)


def _unit_seconds(unit: str) -> int | None:
    u = unit.lower()
    if u == "полчаса":
        return 1800
    if u.startswith(("мин", "хв")) or u in ("м", "min", "m"):
        return 60
    if u.startswith(("час", "год")) or u in ("ч", "h"):
        return 3600
    if u.startswith(("дн", "ден", "дне", "доб")) or u in ("д", "d"):
        return 86400
    if u.startswith(("нед", "тиж")):
        return 604800
    return None


def _clock(match: re.Match[str]) -> tuple[int, int] | None:
    """(час, минута) из групп h/m; (DEFAULT_HOUR, 0), если время не указано; None — если невалидно."""
    if match["h"] is None:
        return DEFAULT_HOUR, 0
    hour, minute = int(match["h"]), int(match["m"])
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _at(base: datetime, hour: int, minute: int) -> datetime:
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def parse_reminder(text: str, now: datetime) -> ParsedReminder | None:
    """Время и текст напоминания. None — не удалось понять время, время в прошлом или нет текста."""
    text = text.strip()
    due: datetime | None = None
    rest = ""

    if match := _RELATIVE_RE.match(text):
        seconds = _unit_seconds(match["unit"])
        if seconds is not None:
            count = int(match["n"]) if match["n"] else 1
            if match["unit"].lower() == "полчаса":
                count = 1
            if 0 < count <= 10_000:
                due, rest = now + timedelta(seconds=seconds * count), match["rest"]

    elif match := _DAY_RE.match(text):
        day = match["day"].lower()
        if day in ("сегодня", "сьогодні") and match["h"] is None:
            return None
        clock = _clock(match)
        if clock is None:
            return None
        offset = {"сегодня": 0, "сьогодні": 0, "завтра": 1, "послезавтра": 2, "післязавтра": 2}[day]
        due, rest = _at(now + timedelta(days=offset), *clock), match["rest"]

    elif match := _AT_RE.match(text):
        clock = _clock(match)
        if clock is None:
            return None
        due = _at(now, *clock)
        if due <= now:
            due += timedelta(days=1)
        rest = match["rest"]

    elif match := _DATE_RE.match(text):
        clock = _clock(match)
        if clock is None:
            return None
        year = int(match["y"]) if match["y"] else now.year
        try:
            due = now.replace(
                year=year, month=int(match["mo"]), day=int(match["d"]),
                hour=clock[0], minute=clock[1], second=0, microsecond=0,
            )  # fmt: skip
        except ValueError:
            return None
        if due <= now and not match["y"]:
            try:
                due = due.replace(year=year + 1)
            except ValueError:
                return None
        rest = match["rest"]

    elif match := _WEEKDAY_RE.match(text):
        clock = _clock(match)
        if clock is None:
            return None
        target = _WEEKDAYS[match["wd"].lower()]
        days_ahead = (target - now.weekday()) % 7
        due = _at(now + timedelta(days=days_ahead), *clock)
        if due <= now:
            due += timedelta(days=7)
        rest = match["rest"]

    rest = rest.strip()
    if due is None or not rest or due <= now:
        return None
    return ParsedReminder(due, rest[:1000])


# ------------------------------------------------------------------ повторяющиеся
# Правила: daily:HH:MM · workdays:HH:MM · weekly:N:HH:MM (N — день недели, 0 = пн) · monthly:D:HH:MM · every:СЕКУНДЫ
MIN_EVERY_SEC = 300
_REPEAT_TIME = r"(?:\s+(?:в\s+|о\s+|at\s+)?(?P<h>\d{1,2})[:.](?P<m>\d{2}))?"
_EVERY_N_RE = re.compile(r"^кажд(?:ые|і)\s+(?:(?P<n>\d+)\s*)?(?P<unit>[a-zа-яёіїєґ]+)\.?\s+(?P<rest>.+)$", _FLAGS)
_DAILY_RE = re.compile(rf"^(?:кажд(?:ый|ий)\s+день|каждое\s+утро|щодня|ежедневно){_REPEAT_TIME}\s+(?P<rest>.+)$", _FLAGS)
_WORKDAYS_RE = re.compile(rf"^(?:по\s+будням|по\s+буднях|кажд(?:ый|ий)\s+будн(?:ий|ій)\s+день){_REPEAT_TIME}\s+(?P<rest>.+)$", _FLAGS)
_MONTHLY_RE = re.compile(
    rf"^(?:кажд(?:ый|ий)\s+месяц|щомісяця|ежемесячно)(?:\s+(?P<d>\d{{1,2}})(?:-?го)?\s*(?:числа|число)?)?{_REPEAT_TIME}\s+(?P<rest>.+)$",
    _FLAGS,
)
_WEEKLY_RE = re.compile(rf"^(?:кажд(?:ый|ую|ое|ий|у)\s+)(?:в\s+)?(?P<wd>{_WEEKDAY_NAMES}){_REPEAT_TIME}\s+(?P<rest>.+)$", _FLAGS)
_WD_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def _hhmm(match: re.Match[str]) -> str | None:
    clock = _clock(match)
    return None if clock is None else f"{clock[0]:02d}:{clock[1]:02d}"


def parse_recurring(text: str, now: datetime) -> ParsedReminder | None:
    """«каждый понедельник 9:00 планёрка» → ParsedReminder с rule. None — это не повторяющееся (или не поняли)."""
    text = text.strip()
    rule = ""
    rest = ""
    if m := _EVERY_N_RE.match(text):
        seconds = _unit_seconds(m["unit"])
        count = int(m["n"]) if m["n"] else 1
        if seconds is None or not 0 < count <= 10_000 or seconds * count < MIN_EVERY_SEC:
            return None
        rule, rest = f"every:{seconds * count}", m["rest"]
        due = now + timedelta(seconds=seconds * count)
        rest = rest.strip()
        return ParsedReminder(due, rest[:1000], rule) if rest else None
    for regex, kind in ((_DAILY_RE, "daily"), (_WORKDAYS_RE, "workdays"), (_MONTHLY_RE, "monthly"), (_WEEKLY_RE, "weekly")):
        m = regex.match(text)
        if m is None:
            continue
        clock = _hhmm(m)
        if clock is None:
            return None
        if kind == "weekly":
            rule = f"weekly:{_WEEKDAYS[m['wd'].lower()]}:{clock}"
        elif kind == "monthly":
            day = int(m["d"]) if m["d"] else now.day
            if not 1 <= day <= 31:
                return None
            rule = f"monthly:{day}:{clock}"
        else:
            rule = f"{kind}:{clock}"
        rest = m["rest"].strip()
        break
    if not rule or not rest or re.fullmatch(r"\d{1,2}[:.]\d{2}", rest):
        return None
    return ParsedReminder(next_due(rule, now), rest[:1000], rule)


def next_due(rule: str, after: datetime, anchor: datetime | None = None) -> datetime:
    """Ближайший момент по правилу строго после `after`."""
    kind, _, tail = rule.partition(":")
    if kind == "every":
        step = max(int(tail), MIN_EVERY_SEC)
        base = anchor or after
        if base > after:
            return base
        k = int((after - base).total_seconds() // step) + 1
        return base + timedelta(seconds=step * k)
    parts = tail.split(":")
    if kind == "weekly":
        weekday, hour, minute = int(parts[0]), int(parts[1]), int(parts[2])
    elif kind == "monthly":
        day, hour, minute = int(parts[0]), int(parts[1]), int(parts[2])
    else:
        hour, minute = int(parts[0]), int(parts[1])
    for offset in range(0, 400):
        day_dt = _at(after + timedelta(days=offset), hour, minute)
        if day_dt <= after:
            continue
        if kind == "daily":
            return day_dt
        if kind == "workdays" and day_dt.weekday() < 5:
            return day_dt
        if kind == "weekly" and day_dt.weekday() == weekday:
            return day_dt
        if kind == "monthly" and day_dt.day == min(day, _month_days(day_dt)):
            return day_dt
    return _at(after + timedelta(days=1), hour, minute)


def _month_days(dt: datetime) -> int:
    return calendar.monthrange(dt.year, dt.month)[1]


def describe_rule(rule: str) -> str:
    kind, _, tail = rule.partition(":")
    parts = tail.split(":")
    try:
        if kind == "daily":
            return f"каждый день в {parts[0]}:{parts[1]}"
        if kind == "workdays":
            return f"по будням в {parts[0]}:{parts[1]}"
        if kind == "weekly":
            return f"каждый {_WD_SHORT[int(parts[0])]} в {parts[1]}:{parts[2]}"
        if kind == "monthly":
            return f"каждое {parts[0]}-е число в {parts[1]}:{parts[2]}"
        if kind == "every":
            sec = int(tail)
            if sec % 86400 == 0:
                return f"каждые {sec // 86400} дн"
            if sec % 3600 == 0:
                return f"каждые {sec // 3600} ч"
            return f"каждые {sec // 60} мин"
    except (IndexError, ValueError):
        pass
    return "повторяется"
