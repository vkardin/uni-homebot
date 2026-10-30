"""🩺 /doctor — самопроверка всего, что подключено: Telegram, модели, перевод, приложения, журналы, SMART, источники."""

from __future__ import annotations

import time
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from homebot import __version__
from homebot.config import CONTAINER_OFF_TEXT, Settings
from homebot.services import backup as backup_svc
from homebot.services import selfupdate
from homebot.services.apps import Apps
from homebot.services.convert import Converter, describe_caps, install_hint
from homebot.services.doctor import FAIL, OFF, OK, WARN, Check, collect, guard, guard_many, ollama_check
from homebot.services.kernel_log import read_kernel_log
from homebot.services.news import NewsService
from homebot.services.prefs import Prefs
from homebot.services.smart import SmartService
from homebot.services.state import StateStore
from homebot.services.tasks import TaskManager
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, MenuCb
from homebot.ui.extra_texts import render_doctor
from homebot.utils.telegram import edit_or_send

router = Router(name="doctor")

_WAIT = "🩺 <b>Проверяю всё подряд…</b>\nОбычно 5–20 секунд."


async def _telegram(bot: Bot) -> Check:
    me = await bot.get_me()
    return Check(OK, "Telegram", f"@{me.username}")


async def _docker(apps: Apps) -> Check:
    assert apps.docker is not None
    containers = await apps.docker.containers()
    bad = [f"{c.name} — {c.problem}" for c in containers if c.problem]
    if bad:
        more = f" и ещё {len(bad) - 4}" if len(bad) > 4 else ""
        return Check(WARN, "Docker", f"{len(containers)} конт.; " + "; ".join(bad[:4]) + more)
    return Check(OK, "Docker", f"{len(containers)} контейнеров, все в порядке")


async def _speedtest(apps: Apps) -> Check:
    assert apps.speedtest is not None
    result = await apps.speedtest.latest()
    if result is None or result.download_mbps is None:
        return Check(WARN, "Speedtest", "замеров пока нет")
    return Check(OK, "Speedtest", f"↓{result.download_mbps:.0f} ↑{(result.upload_mbps or 0):.0f} Мбит/с")


async def _simple(name: str, call: Any, detail: str = "на связи") -> Check:
    await call
    return Check(OK, name, detail)


async def _actual(apps: Apps) -> Check:
    assert apps.actual is not None
    account, categories = await apps.actual.check()
    return Check(OK, "Actual Budget", f"счёт «{account.name}», категорий {categories}")


async def _p2p(apps: Apps) -> Check:
    assert apps.p2p is not None
    quote = await apps.p2p.quote()
    return Check(OK, "Binance P2P", f"{apps.p2p.asset}→{apps.p2p.fiat}: {quote.typical:.2f} (объявлений {quote.total})")


async def _sites(apps: Apps) -> Check:
    assert apps.sites is not None
    count = await apps.sites.check()
    return Check(OK, "changedetection", f"слежек: {count}")


async def _container() -> Check:
    return Check(OFF, "Docker-режим", f"недоступно в Docker: {CONTAINER_OFF_TEXT}. Обновление: install.sh --docker")


async def _selfupdate() -> Check:
    if not selfupdate.updater_installed():
        return Check(WARN, "Обновление из чата", "обновлятор не установлен — выполните обычную установку (update.sh) один раз")
    previous = selfupdate.previous_version()
    return Check(OK, "Обновление из чата", "готово" + (f" · для отката есть v{previous}" if previous else ""))


async def _backup(settings: Settings, store: StateStore) -> Check:
    status = backup_svc.last_status(store)
    items = backup_svc.list_backups(settings.state_dir)
    if not status:
        return Check(WARN, "Бэкап", f"копий ещё нет — первая будет в {settings.backup_hour:02d}:00 или нажмите «Сохранить сейчас»")
    if status.get("ok") is False:
        return Check(WARN, "Бэкап", f"последняя попытка не удалась: {status.get('error', '')}")
    age_h = (time.time() - int(status.get("ts", 0))) / 3600
    if age_h > 36:
        return Check(WARN, "Бэкап", f"последней копии {age_h / 24:.0f} дн — проверьте, что бот работает по расписанию")
    return Check(OK, "Бэкап", f"копий: {len(items)}, последняя {age_h:.0f} ч назад")


async def _journal() -> Check:
    _, error = await read_kernel_log(int(time.time()) - 60)
    if error:
        return Check(WARN, "Журнал ядра", error)
    return Check(OK, "Журнал ядра", "читается")


async def _smart(smart: SmartService) -> Check:
    report = smart.read()
    if report is None:
        return Check(WARN, "SMART", "снимка нет — проверьте homebot-smart.timer")
    if report.error:
        return Check(WARN, "SMART", report.error)
    if report.is_stale():
        return Check(WARN, "SMART", f"снимок устарел ({report.age_sec() / 3600:.0f} ч)")
    return Check(OK, "SMART", f"дисков: {len(report.disks)}")


async def _tools(converter: Converter) -> Check:
    caps = converter.refresh()
    missing = [name for ok, name, _ in describe_caps(caps) if not ok]
    if not missing:
        return Check(OK, "Инструменты", "всё установлено")
    hint = install_hint(caps)
    tail = f": {hint}" if hint else " (обновление поставит их само)"
    return Check(WARN, "Инструменты", f"нет {', '.join(missing)}{tail}")


async def _tasks(tasks: TaskManager) -> Check:
    broken = tasks.broken()
    if not broken:
        return Check(OK, "Фоновые задачи", "работают (упавшие перезапускаются сами)")
    names = "; ".join(f"{title} — {error}" for title, error in broken[:3])
    return Check(FAIL, "Фоновые задачи", f"остановлены после падений: {names}. Нажмите «🔧 Починить»")


async def _build_report(
    bot: Bot,
    settings: Settings,
    apps: Apps,
    prefs: Prefs,
    ollama: Any,
    smart: SmartService,
    news: NewsService,
    store: StateStore,
    converter: Converter,
    tasks: TaskManager,
) -> str:
    checks: list[Any] = [
        guard("Фоновые задачи", _tasks(tasks)),
        guard("Telegram", _telegram(bot)),
        guard_many(
            "Ollama",
            ollama_check(
                ollama,
                news_model=prefs.news().model,
                translate_model=prefs.translate_model(),
                machine_url=settings.libretranslate_url,
            ),
        ),
        *(
            [guard("Docker-режим", _container())]
            if settings.homebot_in_container
            else [guard("Журнал ядра", _journal()), guard("SMART", _smart(smart)), guard("Обновление из чата", _selfupdate())]
        ),
        guard("Бэкап", _backup(settings, store)),
        guard("Инструменты", _tools(converter)),
    ]
    off: list[str] = []
    optional = {
        "Docker": (apps.docker, lambda: _docker(apps)),
        "Speedtest": (apps.speedtest, lambda: _speedtest(apps)),
        "qBittorrent": (apps.qbit, lambda: _simple("qBittorrent", apps.qbit.transfer())),  # type: ignore[union-attr]
        "AdGuard": (apps.adguard, lambda: _simple("AdGuard", apps.adguard.stats())),  # type: ignore[union-attr]
        "Jellyfin": (apps.jellyfin, lambda: _simple("Jellyfin", apps.jellyfin.now_playing())),  # type: ignore[union-attr]
        "Radarr": (apps.radarr, lambda: _simple("Radarr", apps.radarr.queue())),  # type: ignore[union-attr]
        "Sonarr": (apps.sonarr, lambda: _simple("Sonarr", apps.sonarr.queue())),  # type: ignore[union-attr]
        "Actual Budget": (apps.actual, lambda: _actual(apps)),
        "Binance P2P": (apps.p2p, lambda: _p2p(apps)),
        "changedetection": (apps.sites, lambda: _sites(apps)),
    }
    for name, (service, factory) in optional.items():
        if service is None:
            off.append(name)
        else:
            checks.append(guard(name, factory()))
    report = await collect(checks, list(prefs.news().feeds), news.fetch_feed, off)
    return render_doctor(report, __version__)


@router.message(Command("doctor", "check"))
async def cmd_doctor(
    message: Message,
    bot: Bot,
    settings: Settings,
    apps: Apps,
    prefs: Prefs,
    ollama: Any,
    smart: SmartService,
    news: NewsService,
    store: StateStore,
    converter: Converter,
    tasks: TaskManager,
) -> None:
    status = await message.answer(_WAIT)
    text = await _build_report(bot, settings, apps, prefs, ollama, smart, news, store, converter, tasks)
    await status.edit_text(text, reply_markup=kb.doctor_kb(repair=bool(tasks.broken())))


@router.callback_query(MenuCb.filter(F.action == MenuAction.DOCTOR))
async def cb_doctor(
    call: CallbackQuery,
    bot: Bot,
    settings: Settings,
    apps: Apps,
    prefs: Prefs,
    ollama: Any,
    smart: SmartService,
    news: NewsService,
    store: StateStore,
    converter: Converter,
    tasks: TaskManager,
) -> None:
    await call.answer()
    await edit_or_send(call, _WAIT)
    text = await _build_report(bot, settings, apps, prefs, ollama, smart, news, store, converter, tasks)
    await edit_or_send(call, text, kb.doctor_kb(repair=bool(tasks.broken())))


@router.callback_query(MenuCb.filter(F.action == MenuAction.REPAIR))
async def cb_repair(call: CallbackQuery, tasks: TaskManager) -> None:
    count = tasks.repair()
    await call.answer(f"Перезапущено задач: {count}" if count else "Чинить нечего", show_alert=True)
