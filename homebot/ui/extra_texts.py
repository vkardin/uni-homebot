"""Тексты новых разделов 2.6: чистка диска, бесплатные игры, темы, погода, расходы, отчёты.

Без зависимостей от aiogram — проверяются обычными тестами.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import datetime, tzinfo
from typing import Any

from homebot.services.cleanup import MAX_SHOWN, DiskForecast, Watched
from homebot.services.free_games import FreeGame
from homebot.services.kernel_log import KernelProblem
from homebot.services.speed_analysis import PeriodStats, SpeedDrop
from homebot.services.weather_alerts import WeatherEvent
from homebot.ui.style import DIV
from homebot.utils.formatting import bar, fmt_bytes

esc = html.escape


# ------------------------------------------------------------------ чистка диска
def _days_phrase(days: int) -> str:
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    return f"{days} дн. назад"


def render_forecast(forecast: DiskForecast | None) -> str:
    if forecast is None:
        return ""
    if forecast.days_left <= 0:
        return "📈 Диск уже заполнен."
    days = forecast.days_left
    if days < 1:
        when = "меньше чем через сутки"
    elif days < 90:
        when = f"примерно через {days:.0f} дн."
    else:
        when = f"через {days / 30:.0f} мес."
    return f"📈 Растёт на ~{forecast.per_day:.1f} % в сутки — место кончится {when}."


def render_cleanup(
    items: Sequence[Watched],
    *,
    disk_percent: float | None = None,
    disk_free: int | None = None,
    forecast: DiskForecast | None = None,
    min_days: int = 3,
) -> str:
    lines = ["🧹 <b>Чистка диска</b>", DIV]
    if disk_percent is not None:
        free = f" · свободно {fmt_bytes(disk_free)}" if disk_free is not None else ""
        lines.append(f"💾 Диск <code>/</code>: {bar(disk_percent)} {disk_percent:.0f} %{free}")
    if text := render_forecast(forecast):
        lines.append(text)
    shown = list(items)[:MAX_SHOWN]
    if not shown:
        lines += ["", f"Просмотренного и лежащего на диске (старше {min_days} дн.) нет — удалять нечего. 👍"]
        return "\n".join(lines)
    lines += ["", "Уже просмотрено в Jellyfin и всё ещё занимает место:"]
    for number, item in enumerate(shown, 1):
        kind = "📺" if item.kind == "s" else "🎬"
        when = f" · {_days_phrase(item.watched_days)}" if item.watched_days is not None else ""
        lines.append(f"{number}. {kind} {esc(item.label)} — <b>{fmt_bytes(item.size)}</b>{when}")
    total = sum(i.size for i in shown)
    hidden = len(items) - len(shown)
    lines += ["", f"Можно освободить: <b>{fmt_bytes(total)}</b>" + (f" (ещё {hidden} позиций не показаны)" if hidden > 0 else "")]
    lines.append("<i>Удаляется вместе с файлами. Скачать снова можно в любой момент.</i>")
    return "\n".join(lines)


def render_cleanup_confirm(item: Watched) -> str:
    kind = "сериал" if item.kind == "s" else "фильм"
    return (
        f"❓ <b>Удалить {kind} «{esc(item.label)}»?</b>\n"
        f"Освободится {fmt_bytes(item.size)}. Файлы будут удалены с диска.\n"
        "Подтверждение действует 60 секунд."
    )


def render_cleanup_all_confirm(items: Sequence[Watched]) -> str:
    total = sum(i.size for i in items)
    names = "\n".join(f"• {esc(i.label)} — {fmt_bytes(i.size)}" for i in items)
    return f"❓ <b>Удалить всё это ({len(items)} шт., {fmt_bytes(total)})?</b>\n{names}\nПодтверждение действует 60 секунд."


def render_cleanup_result(removed: int, freed: int, errors: Sequence[str]) -> str:
    lines = [f"🗑 Удалено: {removed} · освобождено {fmt_bytes(freed)}"]
    lines += [f"⚠️ {esc(e)}" for e in errors[:3]]
    return "\n".join(lines)


# ------------------------------------------------------------- скорость интернета
def _times(share: float) -> str:
    """0.25 -> «25 % от обычной»."""
    return f"{max(share, 0) * 100:.0f} % от обычной"


def render_speed_drop(drop: SpeedDrop) -> str:
    return (
        "🐢 <b>Интернет просел</b>\n"
        f"Последние замеры подряд: ⬇️ ~{drop.recent:.0f} Мбит/с — {_times(drop.share)} (~{drop.baseline:.0f}).\n"
        "Если так не первый день, это повод писать провайдеру: график и цифры — в разделе Speedtest."
    )


def render_speed_recovered(drop: SpeedDrop) -> str:
    return f"🚀 <b>Скорость вернулась</b>: ⬇️ ~{drop.recent:.0f} Мбит/с при обычных ~{drop.baseline:.0f}."


def _delta(now: float, before: float | None) -> str:
    if not before:
        return ""
    change = (now - before) / before * 100
    if abs(change) < 3:
        return " (как на прошлой неделе)"
    return f" ({change:+.0f} % к прошлой неделе)"


def render_speed_week(this: PeriodStats, prev: PeriodStats | None) -> str:
    lines = [
        "📶 <b>Интернет за неделю</b>",
        f"Замеров: {this.count}" + (f", неудачных: {this.failed}" if this.failed else ""),
        f"⬇️ обычно <b>{this.down_median:.0f}</b> Мбит/с{_delta(this.down_median, prev.down_median if prev else None)}",
        f"⬇️ в плохие часы ≤ {this.down_p10:.0f}, самый медленный замер — {this.down_min:.0f}",
        f"⬆️ среднее {this.up_avg:.0f} Мбит/с" + (f" · пинг {this.ping_avg:.0f} мс" if this.ping_avg is not None else ""),
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------- бесплатные игры
_MONTHS = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


def _when(moment: datetime, tz: tzinfo) -> str:
    local = moment.astimezone(tz)
    return f"{local.day} {_MONTHS[local.month - 1]}, {local:%H:%M}"


def render_free_game(game: FreeGame, tz: tzinfo, now: datetime, *, last_day: bool = False) -> str:
    head = "⏰ <b>Последние сутки раздачи</b>" if last_day else f"🎁 <b>Бесплатно в {esc(game.store)}</b>"
    lines = [head, f"<b>{esc(game.title)}</b>" + (f" (обычно {esc(game.worth)})" if game.worth else "")]
    if game.end is not None:
        left = game.end - now
        hours = int(left.total_seconds() // 3600)
        tail = f" — осталось ~{hours} ч" if 0 < hours < 24 else ""
        lines.append(f"⏳ до {_when(game.end, tz)}{tail}")
    lines.append(f'🔗 <a href="{esc(game.url, quote=True)}">Забрать</a>')
    return "\n".join(lines)


# ------------------------------------------------------------------------ погода
def _weather_line(event: WeatherEvent, tz: tzinfo) -> str:
    at = event.at.astimezone(tz).strftime("%H:%M") if event.at else "?"
    if event.kind == "rain":
        when = "вот-вот" if event.value <= 5 else f"примерно через {event.value:.0f} мин"
        return f"🌧 <b>Скоро дождь</b> — {when}, за ближайший час ~{event.extra:.1f} мм. Возьмите зонт ☂️"
    if event.kind == "frost":
        return (
            f"🥶 <b>Мороз</b>: в ближайшие 12 часов до {event.value:.0f} °C (около {at}). "
            "Тёплая одежда; воду и технику на балконе лучше убрать."
        )
    if event.kind == "heat":
        return f"🥵 <b>Жара</b>: до {event.value:.0f} °C около {at}. Вода, тень, шторы на окнах."
    return f"💨 <b>Сильный ветер</b>: порывы до {event.value:.0f} м/с около {at}. Закройте окна, уберите с балкона лёгкое."


def render_weather_events(events: Sequence[WeatherEvent], tz: tzinfo) -> str:
    return "\n".join(_weather_line(e, tz) for e in events)


# ------------------------------------------------------------------- журнал ядра
def plural(n: int, one: str, few: str, many: str) -> str:
    """1 запись, 2 записи, 5 записей."""
    if 11 <= n % 100 <= 14:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def render_kernel_problems(problems: Sequence[KernelProblem]) -> str:
    lines = ["🧯 <b>Журнал ядра</b>"]
    for problem in problems:
        word = plural(problem.count, "запись", "записи", "записей")
        lines += [
            f"{esc(problem.rule.title)} — {problem.count} {word}",
            f"<pre>{esc(problem.sample)}</pre>",
            f"<i>{esc(problem.rule.advice)}</i>",
        ]
    return "\n".join(lines)


# ------------------------------------------------------------------ ночной режим
def render_night(night: Any) -> str:
    lines = ["🌙 <b>Ночной режим</b>", DIV]
    if night.enabled:
        lines.append(f"Ночь: <b>{night.label}</b>")
    else:
        lines.append("Сейчас выключен: всё приходит со звуком в любое время.")
    lines += [
        "",
        "🔔 <b>Всегда со звуком:</b> ошибки дисков и перегрев, упавшие контейнеры, вход по SSH, "
        "ваши напоминания и ответы на ваши сообщения.",
        "🔕 <b>Ночью без звука:</b> новости, торренты и кино, отчёты о скорости.",
        "☀️ <b>Ждут утра:</b> раздачи игр и погодные события.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- модель перевода
def render_translate_models(current: str, chat_model: str, *, machine: bool) -> str:
    lines = ["🌐 <b>Модель перевода</b>", DIV]
    if machine:
        lines += ["Сейчас переводит LibreTranslate (LIBRETRANSLATE_URL в .env) — модель ниже не используется.", ""]
    lines.append(f"Сейчас: <code>{esc(current or 'как в чате')}</code> (в чате: <code>{esc(chat_model)}</code>)")
    lines += [
        "",
        "Этой моделью бот переводит заголовки новостей и описания без пересказа. Пересказ и чат остаются на своих моделях.",
        "Для перевода хватает маленькой модели (qwen3:1.7b и т. п.): вдвое быстрее и легче для процессора.",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------- самопроверка
_DOCTOR_ICONS = {"ok": "✅", "warn": "⚠️", "fail": "❌", "off": "⚪"}


def render_doctor(report: Any, version: str) -> str:
    problems = report.problems
    head = "все системы в порядке" if not problems else f"есть что поправить: {problems}"
    lines = [f"🩺 <b>Самопроверка UNI {esc(version)}</b>", f"<i>{head} · {report.took:.0f} с</i>", DIV]
    for check in report.checks:
        detail = f" — {esc(check.detail)}" if check.detail else ""
        lines.append(f"{_DOCTOR_ICONS.get(check.status, '•')} <b>{esc(check.name)}</b>{detail}")
    if report.off:
        lines.append(f"⚪ Не подключены: {esc(', '.join(report.off))}")
    if report.feeds:
        broken = [f for f in report.feeds if f.error]
        working = len(report.feeds) - len(broken)
        lines += [DIV, f"📚 <b>Источники</b>: {len(report.feeds)} · работают {working}"]
        for feed in report.feeds:
            name = esc(feed.title[:40] or feed.host)
            if feed.error:
                lines.append(f"❌ {name} — {esc(feed.error)}")
            elif feed.items:
                lines.append(f"✅ {name} — {feed.items} новостей")
            else:
                lines.append(f"⚠️ {name} — лента пустая")
    text = "\n".join(lines)
    return text if len(text) <= 4000 else text[:3990] + "…"
