"""💰 Финансы: Actual Budget в простом виде прямо в боте.

* главный экран — балансы счетов, расход/доход месяца, «к распределению», перерасход по категориям;
* записать трату или доход: кнопки ➖/➕ или просто текстом «кофе 85» (строгий формат, чтобы обычные
  сообщения не принимались за траты); карточка → «Записать»; после записи — «↩️ Отменить» и остаток бюджета;
* последние операции с удалением, бюджет месяца по категориям с листанием месяцев;
* счёт для записи по умолчанию выбирается кнопкой.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from homebot.config import Settings
from homebot.services.actual import Account, ActualService, Category, Txn, shift_month
from homebot.services.apps import Apps
from homebot.services.apps_http import AppError
from homebot.services.expense_parse import parse_expense
from homebot.services.expenses import (
    KEY_ACCOUNT,
    KEY_WORDS,
    Draft,
    DraftBox,
    SavedBox,
    guess_category,
    persist_saved,
    remember_category,
    restore_saved,
)
from homebot.states import FinanceStates
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import ExpenseAction, ExpenseCb, MenuAction, MenuCb
from homebot.ui.finance_texts import (
    ACTUAL_OFF_TEXT,
    render_actual_check,
    render_budget,
    render_card,
    render_delete_confirm,
    render_entry_prompt,
    render_home,
    render_ops,
    render_pick,
    render_saved,
    render_undone,
)
from homebot.ui.texts import render_app_error
from homebot.utils.telegram import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="finance")

_TITLE = "💰 <b>Финансы</b>"
_STALE = "Карточка устарела — напишите трату заново"
_DRAFTS = DraftBox()
_SAVED = SavedBox()
_CACHE_TTL = 300
_OPS_DAYS = 31
_OPS_LIMIT = 10
_MAX_MONTHS_BACK = 24
_cache: dict[str, tuple[float, Any]] = {}
_ops: dict[int, list[Txn]] = {}
_ops_tokens = itertools.count(int(time.time()) % 100_000)


# ------------------------------------------------------------------ данные
async def _cached(name: str, loader: Any) -> Any:
    """Категории, счета и получатели меняются редко — держим их пять минут."""
    hit = _cache.get(name)
    if hit is not None and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    value = await loader()
    _cache[name] = (time.monotonic(), value)
    return value


def _forget_cache() -> None:
    _cache.clear()


async def _categories(actual: ActualService) -> list[Category]:
    rows = await _cached("categories", actual.categories)
    return sorted(rows, key=lambda c: c.name.casefold())


async def _accounts(actual: ActualService) -> list[Account]:
    """Открытые счета: сначала бюджетные, потом остальные; по алфавиту."""
    rows = await _cached("accounts", actual.accounts)
    return sorted((a for a in rows if not a.closed), key=lambda a: (a.offbudget, a.name.casefold()))


async def _payees(actual: ActualService) -> dict[str, str]:
    return await _cached("payees", actual.payees)


async def _default_account(actual: ActualService, store: Any) -> Account:
    saved = store.get(KEY_ACCOUNT)
    if saved:
        found = next((a for a in await _accounts(actual) if a.id == saved), None)
        if found is not None:
            return found
    return await actual.account()


def _today(settings: Settings) -> date:
    return datetime.now(settings.tz).date()


def _name(items: list[Any], item_id: str) -> str:
    return next((i.name for i in items if i.id == item_id), "")


# ------------------------------------------------------------------ экраны
async def _home_view(actual: ActualService, settings: Settings, store: Any) -> tuple[str, InlineKeyboardMarkup]:
    month = _today(settings).strftime("%Y-%m")
    accounts_task = asyncio.ensure_future(actual.accounts(balances=True))
    try:
        budget, month_error = await actual.budget_month(month), None
    except AppError as exc:
        budget, month_error = None, str(exc)
    accounts = await accounts_task
    default = await _default_account(actual, store)
    text = render_home(accounts, budget, default.name, settings.actual_currency, month_error=month_error)
    return text, kb.finance_kb(default.name)


async def _card_view(draft: Draft, actual: ActualService, settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    categories = await _categories(actual)
    account = _name(await _accounts(actual), draft.account_id) or "счёт по умолчанию"
    text = render_card(draft, _name(categories, draft.category_id), account, settings.actual_currency)
    return text, kb.expense_kb(draft.number)


async def _save(draft: Draft, actual: ActualService, settings: Settings, store: Any) -> str:
    """Записывает черновик. Бросает AppError — тогда ничего не записано."""
    await actual.add(draft.to_txn(), draft.account_id or None)
    _cache.pop("payees", None)
    restore_saved(store, _SAVED)  # сначала подтянуть записанное до перезапуска, иначе persist перезапишет его
    _SAVED.add(draft)
    await persist_saved(store, _SAVED)
    if draft.category_id:
        await remember_category(store, draft.parsed.note, draft.category_id)
    remaining = None
    if draft.category_id and not draft.parsed.is_income:
        try:
            remaining = await actual.month_category(draft.day[:7], draft.category_id)
        except AppError as exc:  # остаток — приятное дополнение; запись уже прошла
            log.debug("Finance: cannot read category balance: %s", exc)
    categories = await _categories(actual)
    account = _name(await _accounts(actual), draft.account_id)
    return render_saved(draft, _name(categories, draft.category_id), account, settings.actual_currency, remaining)


async def _ops_view(actual: ActualService, settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    accounts = await _accounts(actual)
    txns = await actual.recent(accounts, _today(settings) - timedelta(days=_OPS_DAYS), _OPS_LIMIT)
    token = next(_ops_tokens)
    _ops[token] = txns
    for old in sorted(_ops)[:-5]:
        _ops.pop(old, None)
    text = render_ops(
        txns, settings.actual_currency, payees=await _payees(actual), categories=await _categories(actual), accounts=accounts
    )
    return text, kb.ops_kb(token, len(txns))


async def _budget_view(actual: ActualService, settings: Settings, offset: int) -> tuple[str, InlineKeyboardMarkup]:
    offset = max(-_MAX_MONTHS_BACK, min(0, offset))
    month = shift_month(_today(settings).strftime("%Y-%m"), offset)
    return render_budget(await actual.budget_month(month), settings.actual_currency), kb.budget_kb(offset)


async def _show(call: CallbackQuery, builder: Any) -> None:
    """Общая обёртка экранов: ошибки Actual показываем понятным текстом с кнопкой назад."""
    try:
        text, markup = await builder
    except AppError as exc:
        await edit_or_send(call, render_app_error(_TITLE, str(exc)), kb.finance_back_kb())
        return
    await edit_or_send(call, text, markup)


# ------------------------------------------------------------------ команды
@router.message(Command("money", "finance"))
async def cmd_money(message: Message, state: FSMContext, apps: Apps, settings: Settings, store: Any) -> None:
    await state.clear()
    if apps.actual is None:
        await message.answer(ACTUAL_OFF_TEXT)
        return
    try:
        text, markup = await _home_view(apps.actual, settings, store)
    except AppError as exc:
        await message.answer(render_app_error(_TITLE, str(exc)))
        return
    await message.answer(text, reply_markup=markup)


@router.message(Command("spend", "expense"))
async def cmd_spend(message: Message, command: CommandObject, state: FSMContext, apps: Apps, settings: Settings, store: Any) -> None:
    if apps.actual is None:
        await message.answer(ACTUAL_OFF_TEXT)
        return
    if command.args:
        await state.clear()
        await _start_draft(message, command.args, apps.actual, settings, store, mode="expense")
        return
    await cmd_money(message, state, apps, settings, store)


@router.message(Command("actual"))
async def cmd_actual(message: Message, apps: Apps, settings: Settings) -> None:
    if apps.actual is None:
        await message.answer(ACTUAL_OFF_TEXT)
        return
    _forget_cache()
    try:
        account, count = await apps.actual.check()
    except AppError as exc:
        await message.answer(render_app_error("💰 <b>Actual Budget</b>", str(exc)))
        return
    await message.answer(render_actual_check(account.name, count, settings.actual_budget_id))


# ------------------------------------------------------------- ввод трат
def _is_expense(message: Message, apps: Apps) -> bool:
    """Фильтр: подключён Actual и текст похож на запись траты. Остальное уйдёт дальше (в fallback)."""
    return apps.actual is not None and parse_expense(message.text or "") is not None


async def _start_draft(
    message: Message, text: str, actual: ActualService, settings: Settings, store: Any, *, mode: str | None
) -> bool:
    """mode: None — свободный текст, «expense»/«income» — после кнопки ➖/➕. False — не понял сумму."""
    parsed = parse_expense(text, allow_bare=mode is not None)
    if parsed is None:
        await message.answer(
            f"{_TITLE}\nНе понял сумму. Пример: <code>кофе 85</code> или <code>85</code>.", reply_markup=kb.entry_cancel_kb()
        )
        return False
    if mode == "income" and not parsed.is_income:
        parsed = replace(parsed, cents=abs(parsed.cents))
    try:
        categories = await _categories(actual)
        learned = dict(store.get(KEY_WORDS) or {})
        guess = guess_category(parsed.note, categories, learned, income=parsed.is_income)
        account = await _default_account(actual, store)
        draft = _DRAFTS.new(parsed, _today(settings).isoformat(), guess, account.id)
        if not settings.expense_confirm and guess:
            _DRAFTS.drop(draft.number)
            await message.answer(await _save(draft, actual, settings, store), reply_markup=kb.expense_saved_kb(draft.number))
            return True
        card, markup = await _card_view(draft, actual, settings)
    except AppError as exc:
        await message.answer(render_app_error(_TITLE, str(exc)))
        return True
    await message.answer(card, reply_markup=markup)
    return True


@router.message(FinanceStates.expense, ~F.text.startswith("/"))
async def on_expense_entry(message: Message, state: FSMContext, apps: Apps, settings: Settings, store: Any) -> None:
    if apps.actual is None:
        await state.clear()
        await message.answer(ACTUAL_OFF_TEXT)
        return
    if await _start_draft(message, message.text or "", apps.actual, settings, store, mode="expense"):
        await state.clear()


@router.message(FinanceStates.income, ~F.text.startswith("/"))
async def on_income_entry(message: Message, state: FSMContext, apps: Apps, settings: Settings, store: Any) -> None:
    if apps.actual is None:
        await state.clear()
        await message.answer(ACTUAL_OFF_TEXT)
        return
    if await _start_draft(message, message.text or "", apps.actual, settings, store, mode="income"):
        await state.clear()


@router.message(StateFilter(None), F.text, _is_expense)
async def on_expense_text(message: Message, apps: Apps, settings: Settings, store: Any) -> None:
    assert apps.actual is not None
    await _start_draft(message, message.text or "", apps.actual, settings, store, mode=None)


# ------------------------------------------------------------------ кнопки
async def _actual_or_alert(call: CallbackQuery, apps: Apps) -> ActualService | None:
    if apps.actual is None:
        await call.answer()
        await edit_or_send(call, ACTUAL_OFF_TEXT)
        return None
    return apps.actual


@router.callback_query(MenuCb.filter(F.action == MenuAction.FINANCE))
@router.callback_query(ExpenseCb.filter(F.action == ExpenseAction.HOME))
async def cb_home(call: CallbackQuery, state: FSMContext, apps: Apps, settings: Settings, store: Any) -> None:
    await state.clear()
    if (actual := await _actual_or_alert(call, apps)) is None:
        return
    await call.answer()
    await _show(call, _home_view(actual, settings, store))


@router.callback_query(ExpenseCb.filter(F.action.in_({ExpenseAction.NEW_EXPENSE, ExpenseAction.NEW_INCOME})))
async def cb_new(call: CallbackQuery, callback_data: ExpenseCb, state: FSMContext, apps: Apps) -> None:
    if await _actual_or_alert(call, apps) is None:
        return
    await call.answer()
    income = callback_data.action == ExpenseAction.NEW_INCOME
    await state.set_state(FinanceStates.income if income else FinanceStates.expense)
    await edit_or_send(call, render_entry_prompt(income), kb.entry_cancel_kb())


@router.callback_query(ExpenseCb.filter(F.action == ExpenseAction.CANCEL))
async def cb_cancel(call: CallbackQuery, callback_data: ExpenseCb) -> None:
    _DRAFTS.drop(callback_data.draft)
    await call.answer("Отменено")
    await edit_or_send(call, "✖️ Не записываю.", kb.finance_back_kb())


@router.callback_query(ExpenseCb.filter(F.action == ExpenseAction.CATS))
async def cb_categories(call: CallbackQuery, callback_data: ExpenseCb, apps: Apps) -> None:
    draft = _DRAFTS.get(callback_data.draft)
    if draft is None or apps.actual is None:
        await call.answer(_STALE, show_alert=True)
        return
    try:
        pool = [c for c in await _categories(apps.actual) if c.is_income == draft.parsed.is_income]
    except AppError as exc:
        await call.answer()
        await edit_or_send(call, render_app_error(_TITLE, str(exc)), kb.finance_back_kb())
        return
    if not pool:
        await call.answer("В Actual нет подходящих категорий", show_alert=True)
        return
    await call.answer()
    what = f"Категория для «{draft.parsed.note}»:" if draft.parsed.note else "Выберите категорию:"
    await edit_or_send(call, render_pick("Категория", what), kb.expense_categories_kb(draft.number, [c.name for c in pool]))


@router.callback_query(ExpenseCb.filter(F.action == ExpenseAction.DRAFT_ACCOUNTS))
async def cb_draft_accounts(call: CallbackQuery, callback_data: ExpenseCb, apps: Apps) -> None:
    draft = _DRAFTS.get(callback_data.draft)
    if draft is None or apps.actual is None:
        await call.answer(_STALE, show_alert=True)
        return
    await call.answer()
    try:
        accounts = await _accounts(apps.actual)
    except AppError as exc:
        await edit_or_send(call, render_app_error(_TITLE, str(exc)), kb.finance_back_kb())
        return
    await edit_or_send(
        call, render_pick("Счёт", "С какого счёта записать?"), kb.expense_accounts_kb(draft.number, [a.name for a in accounts])
    )


@router.callback_query(ExpenseCb.filter(F.action.in_({ExpenseAction.PICK, ExpenseAction.PICK_ACCOUNT})))
async def cb_pick(call: CallbackQuery, callback_data: ExpenseCb, apps: Apps, settings: Settings) -> None:
    draft = _DRAFTS.get(callback_data.draft)
    if draft is None or apps.actual is None:
        await call.answer(_STALE, show_alert=True)
        return
    await call.answer()
    actual = apps.actual
    try:
        if callback_data.idx >= 0 and callback_data.action == ExpenseAction.PICK:
            pool = [c for c in await _categories(actual) if c.is_income == draft.parsed.is_income]
            if callback_data.idx < len(pool):
                draft = _DRAFTS.set_category(draft.number, pool[callback_data.idx].id) or draft
        elif callback_data.idx >= 0:
            accounts = await _accounts(actual)
            if callback_data.idx < len(accounts):
                draft = _DRAFTS.set_account(draft.number, accounts[callback_data.idx].id) or draft
        text, markup = await _card_view(draft, actual, settings)
    except AppError as exc:
        await edit_or_send(call, render_app_error(_TITLE, str(exc)), kb.finance_back_kb())
        return
    await edit_or_send(call, text, markup)


@router.callback_query(ExpenseCb.filter(F.action == ExpenseAction.SAVE))
async def cb_save(call: CallbackQuery, callback_data: ExpenseCb, apps: Apps, settings: Settings, store: Any) -> None:
    draft = _DRAFTS.take(callback_data.draft)  # повторное нажатие ничего не запишет второй раз
    if draft is None or apps.actual is None:
        await call.answer("Карточка устарела или уже записана", show_alert=True)
        return
    await call.answer("Записываю…")
    try:
        text = await _save(draft, apps.actual, settings, store)
    except AppError as exc:
        _DRAFTS.put(draft)  # не записали — карточка остаётся, можно нажать ещё раз
        await edit_or_send(call, render_app_error(_TITLE, str(exc)), kb.expense_kb(draft.number))
        return
    await edit_or_send(call, text, kb.expense_saved_kb(draft.number))


@router.callback_query(ExpenseCb.filter(F.action == ExpenseAction.UNDO))
async def cb_undo(call: CallbackQuery, callback_data: ExpenseCb, apps: Apps, settings: Settings, store: Any) -> None:
    restore_saved(store, _SAVED)
    draft = _SAVED.take(callback_data.draft)
    if draft is None or apps.actual is None:
        await call.answer("Отменить уже нельзя — удалите запись в «📋 Операции»", show_alert=True)
        return
    await call.answer("Отменяю…")
    try:
        account_id = draft.account_id or (await apps.actual.account()).id
        found = await apps.actual.undo(draft.to_txn(), account_id)
    except AppError as exc:
        _SAVED.put_back(draft)
        await persist_saved(store, _SAVED)
        await edit_or_send(call, render_app_error(_TITLE, str(exc)), kb.expense_saved_kb(draft.number))
        return
    await persist_saved(store, _SAVED)
    await edit_or_send(call, render_undone(draft, settings.actual_currency, found), kb.finance_back_kb())


@router.callback_query(ExpenseCb.filter(F.action == ExpenseAction.OPS))
async def cb_ops(call: CallbackQuery, apps: Apps, settings: Settings) -> None:
    if (actual := await _actual_or_alert(call, apps)) is None:
        return
    await call.answer()
    await _show(call, _ops_view(actual, settings))


def _snapshot_txn(callback_data: ExpenseCb) -> Txn | None:
    rows = _ops.get(callback_data.draft)
    if rows is None or not 0 <= callback_data.idx < len(rows):
        return None
    return rows[callback_data.idx]


@router.callback_query(ExpenseCb.filter(F.action == ExpenseAction.DELETE))
async def cb_delete(call: CallbackQuery, callback_data: ExpenseCb, apps: Apps, settings: Settings) -> None:
    txn = _snapshot_txn(callback_data)
    if txn is None or apps.actual is None or not txn.id:
        await call.answer("Список устарел — откройте «📋 Операции» заново", show_alert=True)
        return
    await call.answer()
    try:
        payees, categories = await _payees(apps.actual), await _categories(apps.actual)
    except AppError:
        payees, categories = {}, []
    text = render_delete_confirm(txn, settings.actual_currency, payees=payees, categories=categories)
    await edit_or_send(call, text, kb.delete_confirm_kb(callback_data.draft, callback_data.idx))


@router.callback_query(ExpenseCb.filter(F.action == ExpenseAction.DELETE_OK))
async def cb_delete_ok(call: CallbackQuery, callback_data: ExpenseCb, apps: Apps, settings: Settings) -> None:
    txn = _snapshot_txn(callback_data)
    if txn is None or apps.actual is None or not txn.id:
        await call.answer("Список устарел — откройте «📋 Операции» заново", show_alert=True)
        return
    try:
        await apps.actual.delete(txn.id)
    except AppError as exc:
        await call.answer()
        await edit_or_send(call, render_app_error(_TITLE, str(exc)), kb.finance_back_kb())
        return
    _ops.pop(callback_data.draft, None)  # индексы снимка больше не верны
    await call.answer("🗑 Удалено")
    await _show(call, _ops_view(apps.actual, settings))


@router.callback_query(ExpenseCb.filter(F.action == ExpenseAction.BUDGET))
async def cb_budget(call: CallbackQuery, callback_data: ExpenseCb, apps: Apps, settings: Settings) -> None:
    if (actual := await _actual_or_alert(call, apps)) is None:
        return
    await call.answer()
    await _show(call, _budget_view(actual, settings, callback_data.idx if callback_data.idx <= 0 else 0))


@router.callback_query(ExpenseCb.filter(F.action == ExpenseAction.ACCOUNTS))
async def cb_accounts(call: CallbackQuery, apps: Apps) -> None:
    if (actual := await _actual_or_alert(call, apps)) is None:
        return
    await call.answer()
    try:
        accounts = await _accounts(actual)
    except AppError as exc:
        await edit_or_send(call, render_app_error(_TITLE, str(exc)), kb.finance_back_kb())
        return
    text = render_pick("Счёт для записи", "Куда записывать траты и доходы по умолчанию?")
    await edit_or_send(call, text, kb.default_account_kb([a.name for a in accounts]))


@router.callback_query(ExpenseCb.filter(F.action == ExpenseAction.SET_ACCOUNT))
async def cb_set_account(call: CallbackQuery, callback_data: ExpenseCb, apps: Apps, settings: Settings, store: Any) -> None:
    if (actual := await _actual_or_alert(call, apps)) is None:
        return
    with contextlib.suppress(AppError):
        accounts = await _accounts(actual)
        if 0 <= callback_data.idx < len(accounts):
            await store.set(KEY_ACCOUNT, accounts[callback_data.idx].id)
    await call.answer("Готово")
    await _show(call, _home_view(actual, settings, store))
