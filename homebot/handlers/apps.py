"""Приложения на сервере: Docker, Speedtest Tracker, qBittorrent, AdGuard Home, Jellyfin."""

from __future__ import annotations

import asyncio
import html
import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from homebot.config import Settings
from homebot.services.apps import Apps
from homebot.services.apps_http import AppError
from homebot.services.charts import render_speed_chart
from homebot.services.docker_api import NAME_RE
from homebot.services.logs import EXPLAIN_PROMPT
from homebot.services.ollama import OllamaError, OllamaService
from homebot.services.power import is_fresh
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import App, AppAction, AppCb, MenuAction, MenuCb
from homebot.ui.texts import (
    render_adguard,
    render_app_error,
    render_apps,
    render_container,
    render_docker,
    render_jellyfin,
    render_ollama_error,
    render_qbit,
    render_speedtest,
)
from homebot.utils.formatting import split_message
from homebot.utils.telegram import answer_from_callback, edit_or_send

log = logging.getLogger(__name__)
router = Router(name="apps")
_background: set[asyncio.Task[None]] = set()  # ссылки на фоновые задачи, чтобы их не собрал GC

_APP_KEYS = {
    App.DOCKER: "docker",
    App.SPEEDTEST: "speedtest",
    App.QBIT: "qbit",
    App.ADGUARD: "adguard",
    App.JELLYFIN: "jellyfin",
}
_TITLES = {
    App.DOCKER: "🐳 <b>Docker</b>",
    App.SPEEDTEST: "🚀 <b>Speedtest</b>",
    App.QBIT: "📥 <b>Торренты</b>",
    App.ADGUARD: "🛡 <b>AdGuard</b>",
    App.JELLYFIN: "🎬 <b>Jellyfin</b>",
}
_KEY_TO_APP = {v: k for k, v in _APP_KEYS.items()}


def _hub(apps: Apps) -> tuple[str, InlineKeyboardMarkup]:
    available = apps.available()
    return render_apps(available), kb.apps_kb([_KEY_TO_APP[k] for k in available if k in _KEY_TO_APP])


async def _view(app: App, apps: Apps, settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    """Главный экран приложения. Ошибки сервиса показываются текстом, а не роняют обработчик."""
    try:
        if app == App.DOCKER and apps.docker:
            containers = await apps.docker.containers()
            return render_docker(containers), kb.docker_kb(containers)
        if app == App.SPEEDTEST and apps.speedtest:
            latest, history = await asyncio.gather(apps.speedtest.latest(), apps.speedtest.history(7))
            return render_speedtest(latest, history, settings.tz), kb.speedtest_kb()
        if app == App.QBIT and apps.qbit:
            transfer, torrents = await asyncio.gather(apps.qbit.transfer(), apps.qbit.torrents())
            return render_qbit(transfer, torrents), kb.qbit_kb()
        if app == App.ADGUARD and apps.adguard:
            stats = await apps.adguard.stats()
            return render_adguard(stats), kb.adguard_kb(protection=stats.protection)
        if app == App.JELLYFIN and apps.jellyfin:
            playing, latest = await asyncio.gather(apps.jellyfin.now_playing(), apps.jellyfin.recently_added())
            return render_jellyfin(playing, latest), kb.app_refresh_kb(App.JELLYFIN)
    except AppError as exc:
        return render_app_error(_TITLES[app], str(exc)), kb.app_refresh_kb(app)
    return render_app_error(_TITLES[app], "не подключено — см. .env"), kb.app_refresh_kb(app)


async def _container_view(name: str, apps: Apps) -> tuple[str, InlineKeyboardMarkup]:
    assert apps.docker is not None
    containers = {c.name: c for c in await apps.docker.containers()}
    container = containers.get(name)
    if container is None:
        return render_app_error(_TITLES[App.DOCKER], f"контейнер {name} не найден"), kb.app_refresh_kb(App.DOCKER)
    try:
        logs, error = await apps.docker.logs(name, tail=25), None
    except AppError as exc:
        logs, error = None, str(exc)
    return render_container(container, logs, error), kb.container_kb(name, restart=apps.docker.restart_enabled)


# ------------------------------------------------------------------ навигация
@router.message(Command("apps"))
async def cmd_apps(message: Message, state: FSMContext, apps: Apps) -> None:
    await state.clear()
    text, markup = _hub(apps)
    await message.answer(text, reply_markup=markup)


@router.message(Command("docker", "speed", "torrents", "adguard", "jellyfin"))
async def cmd_app(message: Message, state: FSMContext, apps: Apps, settings: Settings) -> None:
    await state.clear()
    command = (message.text or "").split()[0].lstrip("/").split("@")[0]
    app = {
        "docker": App.DOCKER,
        "speed": App.SPEEDTEST,
        "torrents": App.QBIT,
        "adguard": App.ADGUARD,
        "jellyfin": App.JELLYFIN,
    }[command]
    placeholder = await message.answer("⏳ Загружаю…")
    text, markup = await _view(app, apps, settings)
    await placeholder.edit_text(text, reply_markup=markup)


@router.callback_query(MenuCb.filter(F.action == MenuAction.APPS))
async def cb_apps(call: CallbackQuery, state: FSMContext, apps: Apps) -> None:
    await call.answer()
    await state.clear()
    text, markup = _hub(apps)
    await edit_or_send(call, text, markup)


@router.callback_query(AppCb.filter(F.action == AppAction.VIEW))
async def cb_app_view(call: CallbackQuery, callback_data: AppCb, apps: Apps, settings: Settings) -> None:
    await call.answer()
    text, markup = await _view(callback_data.app, apps, settings)
    await edit_or_send(call, text, markup)


# ----------------------------------------------------------------------- Docker
@router.callback_query(AppCb.filter((F.app == App.DOCKER) & (F.action == AppAction.CONTAINER)))
async def cb_container(call: CallbackQuery, callback_data: AppCb, apps: Apps) -> None:
    if apps.docker is None or not NAME_RE.fullmatch(callback_data.arg):
        await call.answer("Docker не подключён", show_alert=True)
        return
    await call.answer()
    try:
        text, markup = await _container_view(callback_data.arg, apps)
    except AppError as exc:
        text, markup = render_app_error(_TITLES[App.DOCKER], str(exc)), kb.app_refresh_kb(App.DOCKER)
    await edit_or_send(call, text, markup)


@router.callback_query(AppCb.filter((F.app == App.DOCKER) & (F.action == AppAction.RESTART)))
async def cb_container_restart(call: CallbackQuery, callback_data: AppCb, apps: Apps) -> None:
    name = callback_data.arg
    if apps.docker is None or not NAME_RE.fullmatch(name):
        await call.answer("Docker не подключён", show_alert=True)
        return
    if not callback_data.go:
        await call.answer()
        await edit_or_send(
            call,
            f"❓ <b>Перезапустить контейнер {html.escape(name)}?</b>\nПодтверждение действует 60 секунд.",
            kb.container_confirm_kb(name, int(time.time())),
        )
        return
    if not is_fresh(callback_data.ts, time.time()):
        await call.answer("Подтверждение устарело — повторите", show_alert=True)
        return
    await call.answer("Перезапускаю…")
    await edit_or_send(call, f"⏳ Перезапускаю <code>{html.escape(name)}</code>…")
    ok, message = await apps.docker.restart(name)
    try:
        text, markup = await _container_view(name, apps)
    except AppError as exc:
        text, markup = render_app_error(_TITLES[App.DOCKER], str(exc)), kb.app_refresh_kb(App.DOCKER)
    prefix = f"✅ {html.escape(name)}: {html.escape(message)}" if ok else f"❌ {html.escape(name)}: {html.escape(message)}"
    await edit_or_send(call, f"{prefix}\n\n{text}", markup)


@router.callback_query(AppCb.filter((F.app == App.DOCKER) & (F.action == AppAction.EXPLAIN)))
async def cb_container_explain(call: CallbackQuery, callback_data: AppCb, apps: Apps, ollama: OllamaService, bot: Bot) -> None:
    name = callback_data.arg
    if apps.docker is None or not NAME_RE.fullmatch(name):
        await call.answer("Docker не подключён", show_alert=True)
        return
    try:
        logs = await apps.docker.logs(name, tail=80)
    except AppError as exc:
        await call.answer(f"Журнал недоступен: {exc}"[:200], show_alert=True)
        return
    if not logs:
        await call.answer("Журнал пуст", show_alert=True)
        return
    await call.answer("🧠 Разбираю журнал…")
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=call.from_user.id):
            answer = await ollama.ask(EXPLAIN_PROMPT, f"Docker-контейнер: {name}\n\n{logs}")
    except OllamaError as exc:
        await answer_from_callback(call, render_ollama_error(exc))
        return
    for chunk in split_message(f"🧠 Разбор журнала {name}\n\n{answer}") or ["(пустой ответ)"]:
        await answer_from_callback(call, chunk, parse_mode=None)


# -------------------------------------------------------------------- Speedtest
async def _follow_speedtest(message: Message, apps: Apps, settings: Settings, previous_id: int | None) -> None:
    """После запуска замера ждём результат (до ~3 минут) и обновляем сообщение."""
    assert apps.speedtest is not None
    for _ in range(12):
        await asyncio.sleep(15)
        try:
            latest = await apps.speedtest.latest()
        except AppError:
            continue
        if latest is not None and latest.id != previous_id and latest.status.lower() in ("completed", "failed", "skipped"):
            text, markup = await _view(App.SPEEDTEST, apps, settings)
            try:
                await message.edit_text(f"🏁 Замер завершён\n\n{text}", reply_markup=markup)
            except TelegramAPIError:
                pass
            return


@router.callback_query(AppCb.filter((F.app == App.SPEEDTEST) & (F.action == AppAction.RUN)))
async def cb_speedtest_run(call: CallbackQuery, apps: Apps, settings: Settings) -> None:
    if apps.speedtest is None:
        await call.answer("Speedtest не подключён", show_alert=True)
        return
    try:
        before = await apps.speedtest.latest()
        await apps.speedtest.run()
    except AppError as exc:
        await call.answer(f"Не удалось запустить: {exc}"[:200], show_alert=True)
        return
    await call.answer("▶️ Замер запущен")
    await edit_or_send(call, "⏳ Замер скорости идёт, обычно это 30–60 секунд. Сообщение обновится само.", kb.speedtest_kb())
    if isinstance(call.message, Message):
        task = asyncio.create_task(_follow_speedtest(call.message, apps, settings, before.id if before else None))
        _background.add(task)
        task.add_done_callback(_background.discard)


@router.callback_query(AppCb.filter((F.app == App.SPEEDTEST) & (F.action == AppAction.CHART)))
async def cb_speedtest_chart(call: CallbackQuery, apps: Apps, settings: Settings, bot: Bot) -> None:
    if apps.speedtest is None or not isinstance(call.message, Message):
        await call.answer("Speedtest не подключён", show_alert=True)
        return
    await call.answer("📈 Рисую…")
    try:
        history = await apps.speedtest.history(7)
    except AppError as exc:
        await answer_from_callback(call, render_app_error(_TITLES[App.SPEEDTEST], str(exc)))
        return
    points = [(r.created_at, r.download_mbps or 0.0, r.upload_mbps or 0.0) for r in history if r.ok and r.created_at]
    if not points:
        await answer_from_callback(call, "🚀 За 7 дней нет успешных замеров.")
        return
    async with ChatActionSender.upload_photo(bot=bot, chat_id=call.message.chat.id):
        png = await asyncio.to_thread(render_speed_chart, points, tz=settings.tz, title="Скорость интернета · 7 дней")
    await call.message.answer_photo(BufferedInputFile(png, "speed.png"), reply_markup=kb.app_refresh_kb(App.SPEEDTEST))


# ---------------------------------------------------------------------- qBittorrent
@router.callback_query(AppCb.filter((F.app == App.QBIT) & F.action.in_({AppAction.PAUSE, AppAction.RESUME})))
async def cb_qbit_toggle(call: CallbackQuery, callback_data: AppCb, apps: Apps, settings: Settings) -> None:
    if apps.qbit is None:
        await call.answer("qBittorrent не подключён", show_alert=True)
        return
    try:
        if callback_data.action == AppAction.PAUSE:
            await apps.qbit.pause_all()
        else:
            await apps.qbit.resume_all()
    except AppError as exc:
        await call.answer(f"Ошибка: {exc}"[:200], show_alert=True)
        return
    await call.answer("⏸ Всё на паузе" if callback_data.action == AppAction.PAUSE else "▶️ Загрузки продолжены")
    await asyncio.sleep(1)  # qBittorrent меняет состояния не мгновенно
    text, markup = await _view(App.QBIT, apps, settings)
    await edit_or_send(call, text, markup)


# ------------------------------------------------------------------------- AdGuard
@router.callback_query(AppCb.filter((F.app == App.ADGUARD) & F.action.in_({AppAction.OFF, AppAction.ON})))
async def cb_adguard_toggle(call: CallbackQuery, callback_data: AppCb, apps: Apps, settings: Settings) -> None:
    if apps.adguard is None:
        await call.answer("AdGuard не подключён", show_alert=True)
        return
    try:
        if callback_data.action == AppAction.OFF:
            await apps.adguard.set_protection(False, minutes=10)
        else:
            await apps.adguard.set_protection(True)
    except AppError as exc:
        await call.answer(f"Ошибка: {exc}"[:200], show_alert=True)
        return
    await call.answer("Защита выключена на 10 минут" if callback_data.action == AppAction.OFF else "Защита включена")
    text, markup = await _view(App.ADGUARD, apps, settings)
    await edit_or_send(call, text, markup)


async def stop_background_tasks() -> None:
    tasks = [t for t in _background if not t.done()]
    _background.clear()
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
