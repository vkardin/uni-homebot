"""Тексты раздела «💰 Финансы» (Actual Budget). Без aiogram — проверяются обычными тестами."""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence

from homebot.services.actual import Account, BudgetCategory, BudgetMonth, Category, Txn
from homebot.services.expense_parse import format_money
from homebot.services.expenses import Draft
from homebot.ui.style import DIV

esc = html.escape

MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")
MAX_BUDGET_LINES = 20

ACTUAL_OFF_TEXT = (
    "💰 <b>Финансы</b>\n"
    f"{DIV}\n"
    "Actual Budget пока не подключён.\n"
    "Нужны контейнер actual-http-api и переменные ACTUAL_API_KEY и ACTUAL_BUDGET_ID в .env — "
    "пошагово в README, раздел «Финансы»."
)
ENTRY_HINT = "✍️ Чтобы записать трату, просто напишите: <code>кофе 85</code> · доход: <code>+5000 зарплата</code>"


def money(cents: int, currency: str, *, sign: bool = False) -> str:
    return esc(format_money(cents, currency, sign=sign))


def month_title(month: str, *, capital: bool = False) -> str:
    """'2026-10' -> 'октябрь 2026'."""
    try:
        year, mon = (int(x) for x in month.split("-"))
        name = MONTHS[mon - 1]
    except (ValueError, IndexError):
        return esc(month)
    return f"{name.capitalize() if capital else name} {year}"


# --------------------------------------------------------------------- главная
def render_home(
    accounts: Sequence[Account],
    month: BudgetMonth | None,
    default_account: str,
    currency: str,
    *,
    month_error: str | None = None,
) -> str:
    lines = ["💰 <b>Финансы</b>", DIV]
    on_budget = [a for a in accounts if not a.closed and not a.offbudget]
    off_budget = [a for a in accounts if not a.closed and a.offbudget]
    for account in on_budget:
        lines.append(f"💳 {esc(account.name)} — <b>{money(account.balance or 0, currency)}</b>")
    if len(on_budget) > 1:
        lines.append(f"Всего в бюджете: <b>{money(sum(a.balance or 0 for a in on_budget), currency)}</b>")
    if not on_budget:
        lines.append("Открытых счетов в бюджете нет.")
    for account in off_budget:
        lines.append(f"🏦 {esc(account.name)} <i>(вне бюджета)</i> — {money(account.balance or 0, currency)}")

    lines.append(DIV)
    if month is not None:
        lines.append(f"📅 {month_title(month.month, capital=True)}: расход <b>{money(month.spent, currency)}</b>")
        if month.income:
            lines[-1] += f" · доход {money(month.income, currency)}"
        if month.to_budget:
            lines.append(f"🧮 К распределению: {money(month.to_budget, currency)}")
        over = sorted((c for c in month.categories if c.overspent), key=lambda c: c.balance)
        if over:
            items = ", ".join(f"{esc(c.name)} {money(-c.balance, currency)}" for c in over[:4])
            lines.append(f"⚠️ Перерасход: {items}")
    elif month_error:
        lines.append(f"📅 Бюджет месяца недоступен: {esc(month_error)}")
    lines += [DIV, ENTRY_HINT, f"Записываю на: <b>{esc(default_account)}</b>"]
    return "\n".join(lines)


# --------------------------------------------------------------------- запись
def render_entry_prompt(income: bool) -> str:
    if income:
        return "➕ <b>Доход</b>\nНапишите сумму и откуда: <code>5000 зарплата</code> или просто <code>5000</code>."
    return "➖ <b>Расход</b>\nНапишите сумму и на что: <code>кофе 85</code>, <code>такси 1 200</code> или просто <code>85</code>."


def render_card(draft: Draft, category: str, account: str, currency: str) -> str:
    kind = "💰 Доход" if draft.parsed.is_income else "💸 Трата"
    lines = [f"<b>{kind}</b>", DIV, f"Сумма: <b>{money(draft.parsed.cents, currency, sign=True)}</b>"]
    if draft.parsed.note:
        lines.append(f"Описание: {esc(draft.parsed.note)}")
    lines.append(f"Категория: {esc(category) if category else '<i>не выбрана</i>'}")
    lines.append(f"Счёт: {esc(account)} · {esc(draft.day)}")
    return "\n".join(lines)


def render_saved(draft: Draft, category: str, account: str, currency: str, remaining: BudgetCategory | None = None) -> str:
    kind = "Доход записан" if draft.parsed.is_income else "Трата записана"
    note = f" — {esc(draft.parsed.note)}" if draft.parsed.note else ""
    tail = f" · {esc(category)}" if category else ""
    lines = [f"✅ {kind}: <b>{money(draft.parsed.cents, currency, sign=True)}</b>{note}{tail}", f"<i>{esc(account)}</i>"]
    if remaining is not None and not remaining.is_income and (remaining.budgeted or remaining.balance < 0):
        if remaining.balance < 0:
            lines.append(f"⚠️ «{esc(remaining.name or category)}»: перерасход {money(-remaining.balance, currency)} в этом месяце")
        else:
            lines.append(
                f"Осталось в «{esc(remaining.name or category)}»: <b>{money(remaining.balance, currency)}</b>"
                f" из {money(remaining.budgeted, currency)}"
            )
    return "\n".join(lines)


def render_undone(draft: Draft, currency: str, found: bool) -> str:
    if not found:
        return "🤷 Эту запись в Actual уже не нашёл — наверное, её удалили или изменили там."
    note = f" — {esc(draft.parsed.note)}" if draft.parsed.note else ""
    return f"↩️ Отменено: {money(draft.parsed.cents, currency, sign=True)}{note}"


def render_pick(title: str, what: str) -> str:
    return f"💰 <b>{esc(title)}</b>\n{esc(what)}"


# ------------------------------------------------------------------ операции
def _txn_line(
    number: int,
    t: Txn,
    *,
    currency: str,
    payees: Mapping[str, str],
    categories: Mapping[str, str],
    accounts: Mapping[str, str],
    show_account: bool,
) -> str:
    day = f"{t.date[8:10]}.{t.date[5:7]}"
    who = payees.get(t.payee, "")
    if t.is_transfer:
        body = f"↔️ {money(abs(t.cents), currency)} · перевод{(': ' + esc(who)) if who else ''}"
    else:
        body = money(t.cents, currency, sign=True)
        if who:
            body += f" · {esc(who)}"
        category = categories.get(t.category_id)
        if category:
            body += f" · <i>{esc(category)}</i>"
        elif t.is_parent:
            body += " · <i>разбита на части</i>"
    if show_account and t.account_id in accounts:
        body += f" · {esc(accounts[t.account_id])}"
    return f"{number}. {day} · {body}"


def render_ops(
    txns: Sequence[Txn],
    currency: str,
    *,
    payees: Mapping[str, str],
    categories: Sequence[Category],
    accounts: Sequence[Account],
) -> str:
    lines = ["📋 <b>Последние операции</b>", DIV]
    if not txns:
        lines.append("За последний месяц операций нет.")
        return "\n".join(lines)
    names = {c.id: c.name for c in categories}
    account_names = {a.id: a.name for a in accounts}
    multi = len({t.account_id for t in txns}) > 1
    for number, t in enumerate(txns, 1):
        lines.append(
            _txn_line(number, t, currency=currency, payees=payees, categories=names, accounts=account_names, show_account=multi)
        )
    lines += ["", "<i>🗑 — удалить операцию (спрошу подтверждение)</i>"]
    return "\n".join(lines)


def render_delete_confirm(t: Txn, currency: str, *, payees: Mapping[str, str], categories: Sequence[Category]) -> str:
    names = {c.id: c.name for c in categories}
    line = _txn_line(1, t, currency=currency, payees=payees, categories=names, accounts={}, show_account=False)[3:]
    return f"🗑 <b>Удалить операцию?</b>\n{DIV}\n{line}\n\nОна исчезнет и из Actual Budget."


# -------------------------------------------------------------------- бюджет
def _status(c: BudgetCategory) -> str:
    if c.balance < 0:
        return "🔴"
    if c.budgeted <= 0:
        return "⚪"
    return "🟡" if -c.spent >= c.budgeted * 0.8 else "🟢"


def render_budget(month: BudgetMonth, currency: str) -> str:
    lines = [f"📊 <b>Бюджет · {month_title(month.month)}</b>", DIV]
    lines.append(f"Доход {money(month.income, currency)} · расход <b>{money(month.spent, currency)}</b>")
    if month.budgeted:
        lines.append(f"Распределено: {money(month.budgeted, currency)}")
    if month.to_budget:
        lines.append(f"🧮 К распределению: <b>{money(month.to_budget, currency)}</b>")
    rows = [c for c in month.categories if not c.is_income and (c.spent or c.budgeted or c.balance < 0)]
    rows.sort(key=lambda c: (c.balance >= 0, c.spent))  # сначала перерасход, потом самые большие траты
    lines.append(DIV)
    if not rows:
        lines.append("В этом месяце по категориям пусто.")
    for c in rows[:MAX_BUDGET_LINES]:
        spent = money(-c.spent, currency)
        if c.budgeted:
            line = f"{_status(c)} {esc(c.name)} — {spent} / {money(c.budgeted, currency)}"
        else:
            line = f"{_status(c)} {esc(c.name)} — {spent}"
        if c.balance < 0:
            line += f" · <b>−{money(-c.balance, currency)}</b>"
        elif c.budgeted:
            line += f" · ост. {money(c.balance, currency)}"
        lines.append(line)
    if len(rows) > MAX_BUDGET_LINES:
        lines.append(f"…и ещё {len(rows) - MAX_BUDGET_LINES}")
    lines += ["", "<i>🔴 перерасход · 🟡 потрачено ≥80% · 🟢 в пределах · ⚪ без бюджета</i>"]
    return "\n".join(lines)


def render_actual_check(account: str, categories: int, budget: str) -> str:
    return (
        "✅ <b>Actual Budget подключён</b>\n"
        f"{DIV}\nБюджет: <code>{esc(budget)}</code>\nСчёт для трат: <b>{esc(account)}</b>\nКатегорий: {categories}\n\n"
        "Пишите: <code>кофе 85</code>, <code>такси 1 200</code>, <code>+5000 зарплата</code>. Раздел — /money."
    )
