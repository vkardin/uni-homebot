"""Текстовое оформление экранов (HTML-разметка Telegram).

Всё динамическое проходит через `html.escape` — имена служб, процессов, моделей и тексты
ошибок могут содержать `<`, `>` и `&`.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import UTC, datetime, tzinfo
from typing import TYPE_CHECKING

from homebot.services.adguard import AdGuardStats
from homebot.services.db import Note, Reminder, SentNews
from homebot.services.digest import Digest
from homebot.services.docker_api import Container
from homebot.services.history import Stats
from homebot.services.jellyfin import MediaItem, NowPlaying
from homebot.services.network import TailnetStatus
from homebot.services.news import Story, feed_name
from homebot.services.ollama import LoadedModel, OllamaError, OllamaTimeout, OllamaUnavailable
from homebot.services.prefs import NewsPrefs
from homebot.services.qbittorrent import Torrent, Transfer
from homebot.services.reminders import describe_rule
from homebot.services.smart import SmartReport, smart_issues
from homebot.services.speedtest import SpeedResult
from homebot.services.ssh_log import LoginRecord, SshEvent, origin_of
from homebot.services.system import ProcessInfo, SystemSnapshot
from homebot.services.tasks import TaskInfo
from homebot.services.units import UnitStatus
from homebot.services.updates import UpdatesInfo
from homebot.services.weather import WeatherInfo
from homebot.ui.style import DIV, main_menu_text
from homebot.utils.formatting import bar, fmt_bytes, fmt_duration, truncate

if TYPE_CHECKING:
    from homebot.services.brief import Brief

esc = html.escape

MAIN_MENU_TEXT = main_menu_text()
SERVER_MENU_TEXT = f"🖥 <b>Сервер</b>\n{DIV}\nЗа всем присматриваю. Что показать?"
SETTINGS_TEXT = (
    "⚙️ <b>Настройки</b>\n"
    f"{DIV}\n"
    "Всё, что меняется здесь, применяется сразу и сохраняется после перезапуска.\n\n"
    "🗞 <b>Новости</b> — частота, количество, тихие часы, пересказ\n"
    "📚 <b>Источники</b> — какие ленты читать\n"
    "🔔 <b>Уведомления</b> — алерты, SSH, Docker, торренты и т. д.\n"
    "🤖 <b>Модель ИИ</b> — какой моделью Ollama пересказывать и отвечать\n"
    "🌙 <b>Ночной режим</b> — когда уведомления приходят без звука\n"
    "🌐 <b>Модель перевода</b> — лёгкая модель для заголовков и статей"
)
ADD_FEED_TEXT = (
    "📚 Пришлите ссылку на RSS- или Atom-ленту.\n"
    "Часто она выглядит как <code>https://сайт/feed/</code> или <code>https://сайт/rss</code>.\n"
    "Я проверю, что лента открывается, и добавлю её."
)

CHAT_INTRO = (
    "💬 <b>Чат с UNI</b>\n"
    f"{DIV}\n"
    "Пишите — отвечу с помощью локальной нейросети и запомню контекст разговора.\n"
    "Выйти: кнопка ниже или /cancel."
)

SUMMARIZE_INTRO = (
    "📝 <b>Резюме</b>\n"
    f"{DIV}\n"
    "Пришлите текст, <b>ссылку</b> на статью или <b>PDF/текстовый файл</b> — модель сделает краткую выжимку.\n"
    "Выйти: кнопка ниже или /cancel."
)

REMINDER_HELP = (
    "Напишите, когда и о чём напомнить, например:\n"
    "<code>через 30 минут проверить бэкап</code>\n"
    "<code>завтра 9:00 оплатить интернет</code>\n"
    "<code>в 18:30 позвонить маме</code>\n"
    "<code>15.10 10:00 продлить домен</code>\n"
    "<code>в пятницу 19:00 кино</code>\n\n"
    "Повторяющиеся:\n"
    "<code>каждый понедельник 9:00 планёрка</code>\n"
    "<code>каждый день 8:30 таблетки</code>\n"
    "<code>по будням 18:00 отчёт</code>\n"
    "<code>каждый месяц 15 числа 10:00 оплатить</code>\n"
    "<code>каждые 2 часа размяться</code>"
)

NOTE_HELP = "Пришлите текст заметки одним сообщением."

_ORIGIN = {"tailscale": "Tailscale", "lan": "локальная сеть", "local": "localhost", "internet": "⚠️ интернет"}


def _signed(value: float) -> str:
    """Температура со знаком; значения, округляющиеся до нуля, без «-0»."""
    rounded = round(value)
    return f"{rounded:+d}°C" if rounded else "0°C"


# --------------------------------------------------------------------- система
def render_status(snap: SystemSnapshot) -> str:
    lines = [
        "🖥 <b>Состояние сервера</b>",
        DIV,
        f"<i>{snap.taken_at:%d.%m.%Y %H:%M:%S}</i>",
        "",
        f"<b>CPU</b>   {bar(snap.cpu_percent)} {snap.cpu_percent:.0f}% · {snap.cpu_threads} потоков",
    ]
    if snap.load_avg is not None:
        one, five, fifteen = snap.load_avg
        lines.append(f"<b>Load</b>  {one:.2f} · {five:.2f} · {fifteen:.2f}")
    lines.append(
        f"<b>RAM</b>   {bar(snap.ram_percent)} {snap.ram_percent:.0f}% ({fmt_bytes(snap.ram_used)} / {fmt_bytes(snap.ram_total)})"
    )
    if snap.swap_total:
        lines.append(
            f"<b>Swap</b>  {bar(snap.swap_percent)} {snap.swap_percent:.0f}% "
            f"({fmt_bytes(snap.swap_used)} / {fmt_bytes(snap.swap_total)})"
        )
    if snap.temperature is not None:
        temp = snap.temperature
        crit = f", критич. {temp.critical:.0f}" if temp.critical else ""
        lines.append(f"🌡 <b>Температура</b> {temp.current:.0f} °C <i>({esc(temp.sensor)}/{esc(temp.label)}{crit})</i>")
    if snap.disks:
        lines += ["", "💾 <b>Накопители</b>"]
        for disk in snap.disks:
            lines.append(
                f"<code>{esc(disk.mountpoint)}</code> {bar(disk.percent)} {disk.percent:.0f}% "
                f"({fmt_bytes(disk.used)} / {fmt_bytes(disk.total)})"
            )
    lines += ["", f"⏱ Аптайм: {fmt_duration(snap.uptime_sec)}"]
    return "\n".join(lines)


def render_top(processes: Sequence[ProcessInfo]) -> str:
    lines = ["🔝 <b>Топ процессов по памяти</b>", DIV]
    if not processes:
        lines.append("Нет данных (недостаточно прав для чтения процессов).")
    for index, proc in enumerate(processes, start=1):
        lines.append(
            f"{index}. <code>{esc(truncate(proc.name, 30))}</code> — {fmt_bytes(proc.rss)} ({proc.mem_percent:.1f}%) · pid {proc.pid}"
        )
    return "\n".join(lines)


def render_services(statuses: Sequence[UnitStatus]) -> str:
    lines = ["🧩 <b>Службы</b>", DIV]
    if not statuses:
        lines.append("Список пуст — задайте MONITORED_SERVICES в .env.")
    for status in statuses:
        lines.append(f"{status.emoji} <code>{esc(status.name)}</code> — {esc(status.label)}")
    return "\n".join(lines)


def render_tasks(items: Sequence[TaskInfo]) -> str:
    lines = ["⚙️ <b>Фоновые задачи</b>", DIV]
    for item in items:
        state = "🟢 работает" if item.running else "⚪ выключена"
        lines.append(f"<b>{esc(item.title)}</b> — {state}")
        lines.append(f"<i>{esc(item.description)}</i>")
        lines.append("")
    lines.append("Нажмите на кнопку, чтобы включить или выключить задачу.")
    return "\n".join(lines)


# ---------------------------------------------------------------------- Ollama
def _expiry_note(model: LoadedModel) -> str:
    if model.expires_at is None:
        return ""
    try:
        remaining = (model.expires_at - datetime.now(UTC)).total_seconds()
    except TypeError:  # наивная дата без часового пояса
        return ""
    if remaining <= 0:
        return ""
    if remaining > 86_400:
        return ", держится в памяти постоянно"
    return f", выгрузится через {fmt_duration(remaining)}"


def render_ollama_panel(
    *,
    host: str,
    active_model: str,
    loaded: Sequence[LoadedModel],
    error: str | None,
) -> str:
    lines = ["🤖 <b>Ollama</b>", DIV, f"Хост: <code>{esc(host)}</code>"]
    if error is not None:
        lines.append(f"Статус: 🔴 недоступна — {esc(error)}")
    else:
        lines.append("Статус: 🟢 работает")
    lines.append(f"Активная модель: <code>{esc(active_model)}</code>")
    if error is None:
        lines += ["", "<b>Сейчас в памяти:</b>"]
        if not loaded:
            lines.append("ничего не загружено — RAM свободна")
        for model in loaded:
            lines.append(
                f"• <code>{esc(model.name)}</code> — {fmt_bytes(model.size)} "
                f"(в VRAM {fmt_bytes(model.size_vram)}){_expiry_note(model)}"
            )
    return "\n".join(lines)


def render_ollama_error(exc: OllamaError) -> str:
    reason = esc(str(exc))
    if isinstance(exc, OllamaUnavailable):
        return (
            "🔴 <b>Ollama недоступна.</b>\n"
            "Проверьте, что служба запущена (<code>systemctl status ollama</code>) "
            f"и OLLAMA_HOST указан верно.\n<i>{reason}</i>"
        )
    if "not found" in str(exc).lower():
        return (
            f"⚠️ <b>Модель не найдена.</b> <i>{reason}</i>\n"
            "Выберите установленную: /ollama → «🔁 Модель», или скачайте: <code>ollama pull имя</code>"
        )
    if isinstance(exc, OllamaTimeout):
        return f"⏱ <b>Ollama не успела ответить.</b>\n<i>{reason}</i>\nМодель могла быть слишком тяжёлой для сервера."
    return f"⚠️ <b>Ошибка Ollama:</b> {reason}"


# ---------------------------------------------------------------- сводка в часовой подборке
def _system_line(snap: SystemSnapshot) -> str:
    parts = [f"CPU {snap.cpu_percent:.0f}%", f"RAM {snap.ram_percent:.0f}%"]
    disk = next((d for d in snap.disks if d.mountpoint == "/"), None)
    if disk is None and snap.disks:
        disk = max(snap.disks, key=lambda d: d.percent)
    if disk is not None:
        parts.append(f"диск {esc(disk.mountpoint)} {disk.percent:.0f}%")
    if snap.temperature is not None:
        parts.append(f"{snap.temperature.current:.0f} °C")
    parts.append(f"аптайм {fmt_duration(snap.uptime_sec)}")
    return " · ".join(parts)


def _weather_block(info: WeatherInfo, label: str) -> list[str]:
    lines = [
        f"{info.emoji} <b>{esc(label)}</b>",
        f"{_signed(info.temperature)} (ощущается {_signed(info.feels_like)}), {esc(info.description)}, ветер {info.wind_ms:.0f} м/с",
        f"Днём {_signed(info.t_max)}, ночью {_signed(info.t_min)}"
        + (f", осадки до {info.precip_prob}%" if info.precip_prob is not None else ""),
    ]
    if info.forecast:
        lines.append(" · ".join(f"{d.weekday} {d.emoji} {_signed(d.t_min)}…{_signed(d.t_max)}" for d in info.forecast))
    return lines


def _maintenance_line(report: Digest) -> str | None:
    parts: list[str] = []
    if report.updates is not None and report.updates.error is None:
        if report.updates.total:
            security = f" ({report.updates.security} security)" if report.updates.security else ""
            parts.append(f"обновлений: {report.updates.total}{security}")
        else:
            parts.append("система обновлена")
        if report.updates.reboot_required:
            parts.append("⚠️ нужна перезагрузка")
    if report.smart_issues is not None:
        parts.append(f"💽 SMART: {len(report.smart_issues)} проблем(ы)" if report.smart_issues else "💽 диски в порядке")
    return "🛡 " + " · ".join(parts) if parts else None


# ------------------------------------------------------------------ live и графики
def render_live(snap: SystemSnapshot, *, interval: int, remaining: int) -> str:
    return render_status(snap) + f"\n\n📡 <i>Live: обновление каждые {interval} с, ещё {fmt_duration(remaining)}</i>"


def render_chart_caption(stats: Stats | None, period_label: str) -> str:
    if stats is None:
        return f"📈 <b>Графики · {esc(period_label)}</b>\nДанных пока нет — история копится с момента обновления бота."
    lines = [
        f"📈 <b>Графики · {esc(period_label)}</b>",
        f"CPU: среднее {stats.cpu_avg:.0f}%, пик {stats.cpu_max:.0f}%",
        f"RAM: среднее {stats.ram_avg:.0f}%, пик {stats.ram_max:.0f}%",
    ]
    if stats.temp_max is not None and stats.temp_avg is not None:
        lines.append(f"Температура: средняя {stats.temp_avg:.0f} °C, пик {stats.temp_max:.0f} °C")
    if stats.disk_last is not None:
        lines.append(f"Диск /: {stats.disk_last:.0f}%")
    return "\n".join(lines)


# ------------------------------------------------------------------------ сеть
def _ago(moment: datetime | None) -> str:
    if moment is None:
        return "давно"
    return fmt_duration((datetime.now(UTC) - moment).total_seconds()) + " назад"


def render_network(
    *,
    tailnet: TailnetStatus | None,
    tailnet_error: str | None,
    external_ip: str | None,
    rx: float,
    tx: float,
    total_rx: int,
    total_tx: int,
    speed: SpeedResult | None = None,
    speed_note: str | None = None,
    tz: tzinfo | None = None,
) -> str:
    lines = [
        "🌐 <b>Сеть</b>",
        DIV,
        f"Внешний IP: <code>{esc(external_ip)}</code>" if external_ip else "Внешний IP: не определён",
        f"Сейчас: ⬇️ {fmt_bytes(rx)}/с · ⬆️ {fmt_bytes(tx)}/с",
        f"С момента загрузки: ⬇️ {fmt_bytes(total_rx)} · ⬆️ {fmt_bytes(total_tx)}",
    ]
    if speed is not None and speed.ok:
        when = speed.created_at.astimezone(tz).strftime("%d.%m %H:%M") if speed.created_at else "?"
        lines.append(f"🚀 Последний замер ({when}): {_speed_line(speed)}")
    elif speed_note:
        lines.append(f"🚀 Speedtest: {esc(speed_note)}")
    lines.append("")
    if tailnet is None:
        lines.append(f"🔒 <b>Tailscale</b>: недоступен ({esc(tailnet_error or '?')})")
        return "\n".join(lines)
    state = "🟢" if tailnet.backend_state == "Running" else "🟡"
    lines.append(f"🔒 <b>Tailscale</b> {state} {esc(tailnet.backend_state)} · онлайн {tailnet.online_count}/{len(tailnet.nodes)}")
    for node in tailnet.nodes[:20]:
        icon = "🟢" if node.online else "⚪"
        me = " <i>(этот сервер)</i>" if node.is_self else ""
        exit_mark = " · exit-node" if node.exit_node else ""
        seen = "" if node.online else f" · был {_ago(node.last_seen)}"
        lines.append(f"{icon} <b>{esc(node.name)}</b>{me} <code>{esc(node.ip)}</code> {esc(node.os)}{exit_mark}{seen}")
    for warning in tailnet.health[:3]:
        lines.append(f"⚠️ {esc(truncate(warning, 120))}")
    return "\n".join(lines)


# ---------------------------------------------------------------- безопасность
def render_ssh_login(event: SshEvent) -> str:
    origin = _ORIGIN.get(origin_of(event.ip), "?")
    return f"🔑 <b>SSH-вход</b>: <code>{esc(event.user)}</code> с <code>{esc(event.ip)}</code>\n{origin} · {esc(event.method)}"


def render_ssh_failures(total: int, top_ips: Sequence[tuple[str, int]], top_users: Sequence[tuple[str, int]], *, minutes: int) -> str:
    ips = ", ".join(f"<code>{esc(ip)}</code> ×{n}" for ip, n in top_ips)
    users = ", ".join(f"{esc(u)} ×{n}" for u, n in top_users)
    return f"🔒 <b>Неудачные попытки SSH</b>: {total} за {minutes} мин\nIP: {ips}\nЛогины: {users}"


def _smart_lines(report: SmartReport | None) -> list[str]:
    if report is None:
        return ["💽 SMART: не настроен (запустите deploy/update.sh)"]
    if report.error and not report.disks:
        return [f"💽 SMART: {esc(report.error)}"]
    lines = []
    stale = " <i>(данные устарели)</i>" if report.is_stale() else ""
    lines.append(f"💽 <b>Диски</b>{stale}")
    for disk in report.disks:
        icon = "🟢" if disk.passed else ("🔴" if disk.passed is False else "⚪")
        details = []
        if disk.temperature is not None:
            details.append(f"{disk.temperature:.0f} °C")
        if disk.percentage_used is not None:
            details.append(f"износ {disk.percentage_used}%")
        if disk.power_on_hours is not None:
            details.append(f"{disk.power_on_hours // 24} дн. работы")
        model = f" {esc(truncate(disk.model, 30))}" if disk.model else ""
        lines.append(f"{icon} <code>{esc(disk.device)}</code>{model} · {' · '.join(details) or 'нет данных'}")
    for issue in smart_issues(report):
        lines.append(f"⚠️ {esc(issue)}")
    return lines


def render_security(
    *,
    logins: Sequence[LoginRecord],
    logins_error: str | None,
    failed: int,
    failed_error: str | None,
    updates: UpdatesInfo | None,
    smart: SmartReport | None,
    tz: tzinfo | None,
) -> str:
    lines = ["🛡 <b>Безопасность</b>", DIV, "🔑 <b>Последние SSH-входы</b> (7 дней)"]
    if logins_error:
        lines.append(f"журнал недоступен: {esc(logins_error)}")
    elif not logins:
        lines.append("входов не было")
    for record in logins:
        when = record.when.astimezone(tz).strftime("%d.%m %H:%M") if record.when else "?"
        origin = _ORIGIN.get(origin_of(record.ip), "?")
        lines.append(f"{when} · <code>{esc(record.user)}</code> · <code>{esc(record.ip)}</code> · {origin}")
    if failed_error:
        lines.append(f"Неудачные попытки: неизвестно ({esc(failed_error)})")
    else:
        lines.append(f"Неудачных попыток за сутки: <b>{failed}</b>")

    lines += ["", "📦 <b>Обновления</b>"]
    if updates is None or updates.error:
        lines.append(f"не удалось проверить ({esc(updates.error if updates else '?')})")
    else:
        if updates.total:
            sec = f", из них security: <b>{updates.security}</b>" if updates.security else ""
            lines.append(f"Доступно: <b>{updates.total}</b>{sec}")
            lines.append("<code>sudo apt update && sudo apt upgrade</code>")
        else:
            lines.append("Система обновлена ✅")
        if updates.reboot_required:
            pkgs = f" ({esc(', '.join(updates.reboot_packages[:5]))})" if updates.reboot_packages else ""
            lines.append(f"⚠️ Требуется перезагрузка{pkgs}")
    lines += ["", *_smart_lines(smart)]
    return "\n".join(lines)


def render_smart_alert(issues: Sequence[str]) -> str:
    return "💽 <b>Проблемы с дисками</b>\n" + "\n".join(f"⚠️ {esc(i)}" for i in issues)


def render_updates_alert(info: UpdatesInfo) -> str:
    lines = ["📦 <b>Обслуживание сервера</b>"]
    if info.security:
        lines.append(f"Обновления безопасности: <b>{info.security}</b> (всего {info.total})")
        lines.append("<code>sudo apt update && sudo apt upgrade</code>")
    if info.reboot_required:
        lines.append("⚠️ Требуется перезагрузка")
    return "\n".join(lines)


def render_log(unit: str, text: str, error: str | None) -> str:
    header = f"📜 <b>Журнал</b> <code>{esc(unit)}</code>"
    if error:
        return f"{header}\n⚠️ {esc(error)}"
    body = esc(text or "(пусто)")
    if len(body) > 3800:  # экранирование удлиняет текст; хвост журнала важнее начала
        body = "…" + body[-3800:].split("\n", 1)[-1]
    return f"{header}\n<pre>{body}</pre>"


# ------------------------------------------------------------------ управление
def render_power(restartable: Sequence[str], *, power_enabled: bool) -> str:
    lines = ["⚡ <b>Управление</b>", DIV]
    if restartable:
        lines.append("Перезапуск служб (с подтверждением):")
        lines.append(", ".join(f"<code>{esc(u)}</code>" for u in restartable))
    else:
        lines.append("Перезапуск служб отключён (RESTART_SERVICES пуст).")
    lines.append("")
    if power_enabled:
        lines.append("Питание сервера: доступны перезагрузка и выключение.")
    else:
        lines.append("Питание сервера: отключено. Включить: <code>POWER_ENABLED=true</code> в .env и deploy/update.sh.")
    return "\n".join(lines)


def render_confirm(question: str) -> str:
    return f"❓ <b>{esc(question)}</b>\nПодтверждение действует 60 секунд."


# --------------------------------------------------------- напоминания и заметки
def render_reminders(items: Sequence[Reminder], tz: tzinfo | None) -> str:
    lines = ["⏰ <b>Напоминания</b>", DIV]
    if not items:
        lines.append("Активных напоминаний нет.")
    for item in items:
        when = datetime.fromtimestamp(item.due_ts, tz).strftime("%d.%m %H:%M")
        again = f" 🔁 <i>{describe_rule(item.repeat)}</i>" if item.repeat else ""
        lines.append(f"• <b>{when}</b> — {esc(truncate(item.text, 120))}{again}")
    lines += ["", "Добавить: кнопка ниже или <code>/remind завтра 9:00 текст</code>. ❌ — удалить."]
    return "\n".join(lines)


def render_notes(items: Sequence[Note], tz: tzinfo | None) -> str:
    lines = ["📝 <b>Заметки</b>", DIV]
    if not items:
        lines.append("Заметок пока нет.")
    for item in items:
        when = datetime.fromtimestamp(item.ts, tz).strftime("%d.%m")
        lines.append(f"• <i>{when}</i> {esc(truncate(item.text, 300))}")
    lines += ["", "Добавить: кнопка ниже или <code>/note текст</code>. 🗑 — удалить."]
    return "\n".join(lines)


# ------------------------------------------------------------------- приложения
APPS_HINT = (
    "Не подключены: {missing}.\n"
    "Впишите адрес и доступ в <code>/opt/homebot/.env</code> и выполните "
    "<code>sudo bash /opt/homebot/deploy/update.sh</code>."
)
_APP_NAMES = {
    "docker": "Docker",
    "speedtest": "Speedtest",
    "qbit": "qBittorrent",
    "adguard": "AdGuard",
    "jellyfin": "Jellyfin",
    "radarr": "Radarr",
    "sonarr": "Sonarr",
    "actual": "Actual Budget",
}


def render_apps(available: Sequence[str]) -> str:
    lines = ["🧰 <b>Приложения</b>", DIV]
    if available:
        lines.append("Подключены: " + ", ".join(_APP_NAMES.get(a, a) for a in available))  # неизвестное имя не роняет экран
    missing = [name for key, name in _APP_NAMES.items() if key not in available]
    if missing:
        lines += ["", APPS_HINT.format(missing=", ".join(missing))]
    return "\n".join(lines)


def render_app_error(title: str, error: str) -> str:
    return f"{title}\n⚠️ {esc(error)}"


def render_docker(containers: Sequence[Container]) -> str:
    running = sum(1 for c in containers if c.state == "running")
    problems = [c for c in containers if c.problem]
    lines = [f"🐳 <b>Docker</b> · запущено {running} из {len(containers)}", DIV]
    if problems:
        lines += ["", "<b>Требуют внимания:</b>"]
        lines += [f"{c.emoji} <code>{esc(c.name)}</code> — {esc(c.problem or '')}" for c in problems]
    else:
        lines.append("Все контейнеры в порядке ✅")
    lines += ["", "Нажмите на контейнер — статус, журнал, перезапуск."]
    return "\n".join(lines)


def render_container(container: Container, logs: str | None, error: str | None) -> str:
    lines = [
        f"{container.emoji} <b>{esc(container.name)}</b>",
        f"Образ: <code>{esc(container.image)}</code>",
        f"Состояние: {esc(container.state)} · {esc(container.status)}",
    ]
    if container.problem:
        lines.append(f"⚠️ {esc(container.problem)}")
    if error:
        lines.append(f"Журнал недоступен: {esc(error)}")
    elif logs is not None:
        body = esc(logs or "(пусто)")
        budget = 3800 - sum(len(line) for line in lines)
        if len(body) > budget:
            body = "…" + body[-budget:].split("\n", 1)[-1]
        lines.append(f"<pre>{body}</pre>")
    return "\n".join(lines)


def _speed_line(result: SpeedResult) -> str:
    return (
        f"⬇️ <b>{result.download_mbps or 0:.0f}</b> Мбит/с · ⬆️ <b>{result.upload_mbps or 0:.0f}</b> Мбит/с · "
        f"пинг {result.ping_ms or 0:.0f} мс"
    )


def render_speedtest(latest: SpeedResult | None, history: Sequence[SpeedResult], tz: tzinfo | None) -> str:
    lines = ["🚀 <b>Скорость интернета</b>", DIV]
    if latest is None:
        lines.append("Замеров ещё не было.")
    else:
        when = latest.created_at.astimezone(tz).strftime("%d.%m %H:%M") if latest.created_at else "?"
        if latest.ok:
            lines.append(f"Последний замер ({when}):")
            lines.append(_speed_line(latest))
            extra = " · ".join(x for x in (latest.isp, latest.server) if x)
            if extra:
                lines.append(f"<i>{esc(extra)}</i>")
        else:
            lines.append(f"Последний замер ({when}): {esc(latest.status or '?')}")
    good = [r for r in history if r.ok and r.download_mbps is not None]
    if good:
        downs = [r.download_mbps or 0 for r in good]
        ups = [r.upload_mbps or 0 for r in good]
        lines += [
            "",
            f"<b>За 7 дней</b> ({len(good)} замеров):",
            f"⬇️ среднее {sum(downs) / len(downs):.0f}, мин {min(downs):.0f}, макс {max(downs):.0f} Мбит/с",
            f"⬆️ среднее {sum(ups) / len(ups):.0f} Мбит/с",
        ]
        failed = len(history) - len(good)
        if failed:
            lines.append(f"Неудачных замеров: {failed}")
    return "\n".join(lines)


def _eta(seconds: int) -> str:
    return "∞" if seconds <= 0 or seconds >= 8_640_000 else fmt_duration(seconds)


def render_qbit(transfer: Transfer, torrents: Sequence[Torrent]) -> str:
    active = [t for t in torrents if not t.done]
    seeding = sum(1 for t in torrents if t.done)
    lines = [
        "📥 <b>Торренты</b>",
        DIV,
        f"⬇️ {fmt_bytes(transfer.dl_speed)}/с · ⬆️ {fmt_bytes(transfer.up_speed)}/с",
        f"За сессию: ⬇️ {fmt_bytes(transfer.dl_session)} · ⬆️ {fmt_bytes(transfer.up_session)}",
        f"Загружается: {len(active)} · готово: {seeding}",
        "",
    ]
    for t in active[:8]:
        speed = f" · {fmt_bytes(t.dlspeed)}/с · осталось {_eta(t.eta)}" if t.dlspeed else ""
        lines.append(f"{t.emoji} {esc(truncate(t.name, 45))}\n    {bar(t.progress * 100, 8)} {t.progress * 100:.0f}%{speed}")
    if not active:
        lines.append("Активных загрузок нет.")
    return "\n".join(lines)


def render_adguard(stats: AdGuardStats) -> str:
    if stats.protection:
        state = "🟢 защита включена"
    elif stats.disabled_for_ms:
        state = f"⏸ защита выключена ещё на {fmt_duration(stats.disabled_for_ms / 1000)}"
    else:
        state = "🔴 защита выключена"
    lines = [
        f"🛡 <b>AdGuard Home</b> {esc(stats.version)}",
        state,
        "",
        f"Запросов: <b>{stats.queries:,}</b>".replace(",", " "),
        f"Заблокировано: <b>{stats.blocked:,}</b> ({stats.blocked_percent:.1f}%)".replace(",", " "),
        f"Среднее время ответа: {stats.avg_ms:.0f} мс",
    ]
    if stats.top_blocked:
        lines += ["", "<b>Чаще всего блокируется:</b>"]
        lines += [f"• <code>{esc(truncate(domain, 45))}</code> — {count}" for domain, count in stats.top_blocked]
    return "\n".join(lines)


def render_jellyfin(playing: Sequence[NowPlaying], latest: Sequence[MediaItem]) -> str:
    lines = ["🎬 <b>Jellyfin</b>", DIV, "<b>Сейчас смотрят:</b>"]
    if not playing:
        lines.append("никто")
    for p in playing:
        progress = f" · {p.progress:.0f}%" if p.progress is not None else ""
        pause = " ⏸" if p.paused else ""
        lines.append(f"▶️ {esc(p.user)} ({esc(p.device)}): {esc(truncate(p.title, 60))}{progress}{pause}")
    lines += ["", "<b>Недавно добавлено:</b>"]
    if not latest:
        lines.append("пусто")
    for item in latest:
        year = f" ({item.year})" if item.year else ""
        icon = "🎞" if item.kind == "Movie" else "📺"
        lines.append(f"{icon} {esc(truncate(item.title, 70))}{year}")
    return "\n".join(lines)


# ------------------------------------------------------------------- новости
_NUMBERS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")


def _news_status(prefs: NewsPrefs) -> str:
    state = f"🟢 {prefs.interval_label}" if prefs.enabled else "⚪ выключена"
    quiet = f" · тихо {prefs.quiet_from:02d}–{prefs.quiet_to:02d}" if prefs.quiet else ""
    return f"Рассылка: {state} · по {prefs.count}{quiet}"


def render_news_screen(
    prefs: NewsPrefs,
    recent: Sequence[SentNews],
    last_run: datetime | None,
    tz: tzinfo | None,
) -> str:
    lines = [
        "🗞 <b>Новости</b>",
        DIV,
        _news_status(prefs),
        f"Источников: {len(prefs.feeds)} · пересказ ИИ: {'вкл' if prefs.retell else 'выкл'}",
    ]
    if last_run is not None:
        lines.append(f"Последняя проверка: {last_run.astimezone(tz):%d.%m %H:%M}")
    if not prefs.feeds:
        lines += ["", "⚠️ Нет источников — добавьте их: 📚 Источники → 📦 Готовые подборки."]
    if recent:
        lines += ["", "<b>Последние присланные:</b>"]
        for item in recent:
            when = datetime.fromtimestamp(item.sent_ts, tz).strftime("%H:%M")
            title = _link(esc(truncate(item.title, 80)), item.link)
            lines.append(f"• {title} <i>({esc(feed_name(item.feed))}, {when})</i>")
    lines += ["", "«⚡ Свежие сейчас» — собрать новые новости, не дожидаясь рассылки."]
    return "\n".join(lines)


def render_news_settings(prefs: NewsPrefs) -> str:
    return (
        "⚙️ <b>Настройки новостей</b>\n"
        f"{DIV}\n"
        f"{_news_status(prefs)}\n"
        f"Модель: {esc(prefs.model or 'как в чате')} · {prefs.mode_label}\n\n"
        "Нажимайте на кнопки — значения переключаются по кругу.\n"
        "• <b>Тихие часы</b>: ночью обновлений нет, утром — новая сводка с главным за ночь.\n"
        "• <b>Пересказ ИИ</b>: модель пишет по-русски 1–2 предложения о каждой новости "
        "(иностранные источники тоже переводятся). Без него — заголовки и описания как есть.\n"
        "• <b>Читать статью</b>: если в ленте нет описания, бот читает начало статьи, чтобы модель не додумывала.\n"
        "• <b>Модель</b>: своя модель для новостей — переключения в чате на неё не влияют.\n"
        "• <b>📌 Одно сообщение на день</b>: утром приходит новая сводка, дальше UNI тихо обновляет её каждый час."
    )


def render_news_models(current: str, chat_model: str) -> str:
    return (
        f"🤖 <b>Модель для новостей</b>\n{DIV}\n"
        f"Сейчас: <code>{esc(current or 'как в чате')}</code> (в чате: <code>{esc(chat_model)}</code>)\n\n"
        "Для пересказа на русском лучше всего подходят qwen3.5:4b и gemma3:4b. "
        "Маленькие модели (до 2B) могут отвечать по-английски или путать факты."
    )


def render_feeds(feeds: Sequence[str]) -> str:
    lines = ["📚 <b>Источники новостей</b>", DIV]
    if not feeds:
        lines.append("Пока пусто.")
    for url in feeds:
        lines.append(f"• <b>{esc(feed_name(url))}</b> — <code>{esc(truncate(url, 70))}</code>")
    lines += ["", "❌ — удалить источник. Новые: по ссылке или из готовых подборок."]
    return "\n".join(lines)


def render_presets() -> str:
    return (
        "📦 <b>Готовые подборки</b>\n"
        f"{DIV}\n"
        "Нажмите на тему — её ленты будут проверены и добавлены (✅ — уже добавлено).\n"
        "Иностранные ленты модель перескажет по-русски."
    )


# ------------------------------------------------------------------ сводка UNI
_BRIEF_BUDGET = 3950
_SHORT_SUMMARY = 220


def _link(title: str, link: str | None) -> str:
    """Заголовок-ссылка на статью (без ссылки — просто текст)."""
    return f'<a href="{esc(link, quote=True)}">{title}</a>' if link else title


def _brief_story(index: int, story: Story, summary_limit: int) -> str:
    title = _link(esc(story.title), story.link)
    text = esc(truncate(story.text, summary_limit)) if story.text and summary_limit else ""
    body = f"\n{text}" if text else ""
    return f"{_NUMBERS[index % len(_NUMBERS)]} <b>{title}</b>{body}"


def _earlier_line(item: SentNews, tz: tzinfo | None) -> str:
    when = datetime.fromtimestamp(item.sent_ts, tz).strftime("%H:%M")
    title = _link(esc(truncate(item.title, 90)), item.link)
    return f"• {title} <i>{when}</i>"


def render_brief(brief: Brief, tz: tzinfo | None) -> str:
    """Порядок: погода и курс → новости кратко → сервер. Влезает в лимит Telegram за счёт сокращения новостей."""
    report = brief.report
    top = [f"💠 <b>UNI · сводка</b> · {brief.created.astimezone(tz):%d.%m %H:%M}", DIV]
    if report.weather is not None:
        top += _weather_block(report.weather, report.weather_label)
    elif report.weather_error:
        top.append(f"🌦 погода недоступна ({esc(report.weather_error)})")
    if report.rates:
        rates = " · ".join(f"{r.code} <b>{r.rate:.2f}</b>" for r in report.rates)
        top.append(f"💱 {rates} <i>(НБУ)</i>")

    server = [DIV, "🖥 <b>Сервер</b>"]
    server.append(_system_line(report.system) if report.system is not None else "метрики недоступны")
    if report.services:
        server.append(" ".join(f"{s.emoji} {esc(s.name)}" for s in report.services))
    if maintenance := _maintenance_line(report):
        server.append(maintenance)
    footer = f"<i>Обновлено {brief.created.astimezone(tz):%H:%M}"
    footer += f" · следующее в {brief.next_at.astimezone(tz):%H:%M}</i>" if brief.next_at else "</i>"
    server.append(footer)

    def news_block(summary_limit: int, earlier_count: int) -> list[str]:
        lines = [DIV]
        if brief.stories:
            lines.append(f"🗞 <b>Новости за час</b> · {len(brief.stories)}")
            lines += [_brief_story(i, s, summary_limit) for i, s in enumerate(brief.stories)]
        elif brief.collected:
            lines.append("🗞 <b>Новости</b>: за этот час нового нет")
        else:
            lines.append("🗞 <b>Новости</b>")
        if brief.news_errors:
            lines.append(f"<i>недоступны: {esc(', '.join(brief.news_errors[:3]))}</i>")
        if brief.earlier and earlier_count:
            lines.append("<i>Ранее сегодня:</i>")
            lines += [_earlier_line(n, tz) for n in brief.earlier[:earlier_count]]
        return lines

    summary_limit, earlier_count = _SHORT_SUMMARY, 10
    while True:
        text = "\n".join([*top, *news_block(summary_limit, earlier_count), *server])
        if len(text) <= _BRIEF_BUDGET:
            return text
        if earlier_count > 0:
            earlier_count -= 2 if earlier_count > 1 else 1
        elif summary_limit > 80:
            summary_limit -= 40
        elif summary_limit:
            summary_limit = 0
        else:
            return text[: _BRIEF_BUDGET - 1] + "…"
