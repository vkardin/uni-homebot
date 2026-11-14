"""🔧 Ремонт того, что нашла самопроверка (/doctor): устаревшие ключи .env, контейнеры, логин qBittorrent."""

from __future__ import annotations

import asyncio
import html
from contextlib import suppress
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from homebot.config import Settings
from homebot.services import fixes
from homebot.services.apps import Apps
from homebot.services.apps_http import AppError
from homebot.services.autofix import ContainerFixer, Result, broken
from homebot.states import AutofixStates
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import AutofixCb
from homebot.utils.telegram import edit_or_send

router = Router(name="autofix")
esc = html.escape
CHECK_TIMEOUT = 20


# ------------------------------------------------------------------ что предложить в /doctor
async def offers(settings: Settings, apps: Apps, fixer: ContainerFixer | None, docker_problems: bool) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    legacy = fixes.env_report(settings)["legacy"]
    if legacy:
        out.append(("env", f"🧹 .env: убрать устаревшие ключи ({len(legacy)})"))
    if docker_problems and fixer is not None and fixer.available:
        out.append(("dock", "🐳 Починить контейнеры"))
    if apps.qbit is not None and apps.qbit.auth_failed:
        out.append(("qbit", "🔑 Ввести логин qBittorrent"))
    return out


# ------------------------------------------------------------------ .env
def render_env(report: dict[str, Any]) -> str:
    lines = ["🧹 <b>.env: незнакомые ключи</b>"]
    if report["legacy"]:
        groups: dict[str, list[str]] = {}
        for item in report["legacy"]:
            groups.setdefault(item["reason"], []).append(item["key"])
        lines += ["", "Устаревшие — этих функций в UNI больше нет, бот их не читает:"]
        lines += [f"• <code>{esc(', '.join(keys))}</code> — {esc(reason)}" for reason, keys in groups.items()]
    if report["other"]:
        lines += ["", "Не узнаю (проверьте сами — их не трогаю):"]
        for item in report["other"]:
            guess = f" — возможно, <code>{esc(item['guess'])}</code>?" if item["guess"] else ""
            lines.append(f"• <code>{esc(item['key'])}</code>{guess}")
    if not report["legacy"] and not report["other"]:
        lines += ["", "Все ключи знакомы 👍"]
    elif report["legacy"]:
        if report["writable"]:
            lines += ["", "«Убрать» удалит только устаревшие строки. Перед этим сохраню копию .env."]
        else:
            lines += ["", f"⚠️ {esc(report['hint'])}"]
    return "\n".join(lines)


@router.callback_query(AutofixCb.filter(F.act == "env"))
async def cb_env(call: CallbackQuery, settings: Settings) -> None:
    await call.answer()
    report = fixes.env_report(settings)
    extra = [("env_rm", "🧹 Убрать устаревшие")] if report["legacy"] and report["writable"] else []
    await edit_or_send(call, render_env(report), kb.autofix_back_kb(extra=extra))


@router.callback_query(AutofixCb.filter(F.act == "env_rm"))
async def cb_env_remove(call: CallbackQuery, settings: Settings) -> None:
    try:
        removed, backup = fixes.remove_legacy(settings)
    except OSError as exc:
        await call.answer(f"Не вышло: {exc}"[:190], show_alert=True)
        return
    await call.answer()
    if not removed:
        text = "🧹 Устаревших ключей в .env уже нет."
    else:
        text = (
            f"🧹 <b>.env почищен</b>: убрал {len(removed)} — <code>{esc(', '.join(removed))}</code>.\n"
            f"Копия до правки: <code>{esc(str(backup))}</code>. Перезапуск не нужен."
        )
    await edit_or_send(call, text, kb.autofix_back_kb())


# ------------------------------------------------------------------ контейнеры
def render_results(results: list[Result]) -> str:
    if not results:
        return "🐳 Чинить нечего: все контейнеры работают."
    lines = ["🐳 <b>Ремонт контейнеров</b>"]
    for r in results:
        who = f"<code>{esc(r.name)}</code>" + (" (зависимость)" if r.dependency else "")
        lines.append(f"{'✅' if r.ok else '❌'} {who}: {esc(r.detail)}")
    if any(not r.ok for r in results):
        lines += ["", "Журнал — в 🐳 Docker (кнопка «📜» у контейнера)."]
    return "\n".join(lines)


async def repair_containers(apps: Apps, settings: Settings, fixer: ContainerFixer) -> list[Result]:
    assert apps.docker is not None
    ignored = settings.docker_ignored
    names = [c.name for c in await apps.docker.containers() if broken(c) and c.name not in ignored]
    return await fixer.fix(names, count=False)  # по кнопке: лимит авторемонта не расходуем


@router.callback_query(AutofixCb.filter(F.act == "dock"))
async def cb_docker(call: CallbackQuery, apps: Apps, settings: Settings, fixer: ContainerFixer | None) -> None:
    if apps.docker is None or fixer is None or not fixer.available:
        await call.answer("Перезапуск контейнеров недоступен", show_alert=True)
        return
    await call.answer()
    await edit_or_send(call, "🐳 <b>Чиню контейнеры…</b>\nЗависимости — первыми. Обычно до пары минут.")
    try:
        results = await repair_containers(apps, settings, fixer)
    except AppError as exc:
        await edit_or_send(call, f"❌ Docker не отвечает: {esc(str(exc))}", kb.autofix_back_kb())
        return
    await edit_or_send(call, render_results(results), kb.autofix_back_kb())


# ------------------------------------------------------------------ qBittorrent
def _cancel_kb() -> Any:
    return kb.autofix_back_kb(extra=[("cancel", "✖️ Отмена")])


@router.callback_query(AutofixCb.filter(F.act == "qbit"))
async def cb_qbit(call: CallbackQuery, apps: Apps, state: FSMContext) -> None:
    if apps.qbit is None:
        await call.answer("qBittorrent не подключён", show_alert=True)
        return
    await call.answer()
    await state.set_state(AutofixStates.qbit_user)
    current = f" (сейчас <code>{esc(apps.qbit.username)}</code>)" if apps.qbit.username else ""
    await edit_or_send(
        call,
        f"🔑 <b>Вход в qBittorrent</b> · {esc(apps.qbit.base_url)}\nПришлите логин веб-интерфейса{current}. "
        "Потом спрошу пароль, проверю вход и только тогда сохраню.",
        _cancel_kb(),
    )


@router.callback_query(AutofixCb.filter(F.act == "cancel"))
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer("Отменено")
    await edit_or_send(call, "Отменено. Ничего не менял.", kb.autofix_back_kb())


@router.message(AutofixStates.qbit_user, F.text, ~F.text.startswith("/"))
async def on_qbit_user(message: Message, state: FSMContext) -> None:
    username = (message.text or "").strip()
    if not username or len(username) > 128:
        await message.answer("Нужен логин — одна строка.", reply_markup=_cancel_kb())
        return
    await state.update_data(qbit_user=username)
    await state.set_state(AutofixStates.qbit_pass)
    await message.answer("Теперь пароль. Сообщение с ним сразу удалю.", reply_markup=_cancel_kb())


@router.message(AutofixStates.qbit_pass, F.text, ~F.text.startswith("/"))
async def on_qbit_pass(message: Message, state: FSMContext, settings: Settings, apps: Apps) -> None:
    password = message.text or ""
    with suppress(TelegramAPIError):
        await message.delete()
    username = str((await state.get_data()).get("qbit_user", ""))
    await state.clear()
    wait = await message.answer("🔑 Проверяю вход в qBittorrent…")
    try:
        saved = await asyncio.wait_for(fixes.qbit_login(settings, apps, username, password), CHECK_TIMEOUT)
    except (AppError, ValueError, TimeoutError) as exc:
        reason = str(exc) or "нет ответа"
        await wait.edit_text(
            f"❌ Вход не удался: {esc(reason)}.\nНичего не сохранил. qBittorrent после нескольких ошибок подряд "
            "блокирует вход на время — проверьте данные в его веб-интерфейсе.",
            reply_markup=kb.autofix_back_kb(extra=[("qbit", "🔑 Ещё раз")]),
        )
        return
    if saved.where == "env":
        tail = f"Записал в .env (копия до правки: <code>{esc(str(saved.backup))}</code>)."
    elif saved.where == "chat":
        tail = "Сохранил в настройках подключения."
    else:
        tail = f"Работает до перезапуска бота, но в .env не записал: {esc(saved.note)}."
    await wait.edit_text(f"✅ <b>qBittorrent</b>: вход работает. {tail}", reply_markup=kb.autofix_back_kb())
