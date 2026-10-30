"""Actual Budget через контейнер-обёртку actual-http-api (у самого Actual HTTP API нет).

Пути и форматы сверены с исходниками jhonderson/actual-http-api (src/v1/routes): префикс `/v1`,
ключ в заголовке `x-api-key`, пароль шифрования бюджета — `budget-encryption-password`, ответы `{"data": ...}`,
ошибки `{"error": "..."}`. Суммы — целые числа в копейках, расход отрицательный.

Создание операции возвращает только `{"message": "ok"}` (без id). Чтобы операцию можно было отменить,
бот пишет в заметку пометку `UNI` и потом находит свою запись по дате, сумме, категории и пометке.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

import aiohttp

from homebot.services.apps_http import AppError, request

PATH_ACCOUNTS = "/v1/budgets/{budget}/accounts"
PATH_CATEGORIES = "/v1/budgets/{budget}/categories"
PATH_PAYEES = "/v1/budgets/{budget}/payees"
PATH_TRANSACTIONS = "/v1/budgets/{budget}/accounts/{account}/transactions"
PATH_TRANSACTION = "/v1/budgets/{budget}/transactions/{txn}"
PATH_MONTH = "/v1/budgets/{budget}/months/{month}"
PATH_MONTH_CATEGORY = "/v1/budgets/{budget}/months/{month}/categories/{category}"

NOTE_MARK = "UNI"  # пометка в заметке операции: так бот узнаёт свои записи (для «Отменить»)
TIMEOUT = 60  # первый запрос после старта обёртки скачивает бюджет — это небыстро


@dataclass(frozen=True, slots=True)
class Account:
    id: str
    name: str
    closed: bool = False
    offbudget: bool = False
    balance: int | None = None  # копейки; None — баланс не запрашивали


@dataclass(frozen=True, slots=True)
class Category:
    id: str
    name: str
    is_income: bool = False
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class Txn:
    date: str  # ГГГГ-ММ-ДД
    cents: int
    category_id: str = ""
    payee: str = ""  # имя (при создании) или id получателя (из Actual)
    notes: str = ""
    id: str = ""
    account_id: str = ""
    transfer_id: str = ""
    is_parent: bool = False

    @property
    def is_transfer(self) -> bool:
        return bool(self.transfer_id)

    @property
    def mine(self) -> bool:
        return self.notes.startswith(NOTE_MARK)


@dataclass(frozen=True, slots=True)
class BudgetCategory:
    id: str
    name: str
    group: str
    budgeted: int = 0
    spent: int = 0  # отрицательное — потрачено
    balance: int = 0  # остаток (с переносом прошлых месяцев)
    is_income: bool = False
    received: int = 0

    @property
    def overspent(self) -> bool:
        return not self.is_income and self.balance < 0


@dataclass(frozen=True, slots=True)
class BudgetMonth:
    month: str  # ГГГГ-ММ
    income: int
    spent: int  # положительное число
    budgeted: int
    to_budget: int  # «К распределению»
    categories: tuple[BudgetCategory, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------- разбор
def _items(payload: Any) -> list[dict[str, Any]]:
    """Обёртка отдаёт `{"data": [...]}`; на всякий случай принимаем и голый список."""
    if isinstance(payload, dict):
        payload = payload.get("data")
    if not isinstance(payload, list):
        raise AppError("Actual ответил в неожиданном формате")
    return [row for row in payload if isinstance(row, dict)]


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def parse_accounts(payload: Any) -> list[Account]:
    rows: list[Account] = []
    for r in _items(payload):
        if not r.get("id"):
            continue
        raw_balance = r.get("workingBalance", r.get("balance_current"))
        balance = _int(raw_balance) if raw_balance is not None else None
        rows.append(Account(str(r["id"]), str(r.get("name") or r["id"]), bool(r.get("closed")), bool(r.get("offbudget")), balance))
    return rows


def parse_categories(payload: Any) -> list[Category]:
    return [
        Category(str(r["id"]), str(r.get("name") or r["id"]), bool(r.get("is_income")), bool(r.get("hidden")))
        for r in _items(payload)
        if r.get("id")
    ]


def parse_payees(payload: Any) -> dict[str, str]:
    return {str(r["id"]): str(r.get("name") or "") for r in _items(payload) if r.get("id")}


def parse_transactions(payload: Any) -> list[Txn]:
    rows: list[Txn] = []
    for r in _items(payload):
        amount = r.get("amount")
        if not isinstance(amount, int | float) or isinstance(amount, bool) or not r.get("date"):
            continue
        rows.append(
            Txn(
                date=str(r["date"])[:10],
                cents=int(amount),
                category_id=str(r.get("category") or ""),
                payee=str(r.get("payee") or r.get("payee_name") or ""),
                notes=str(r.get("notes") or ""),
                id=str(r.get("id") or ""),
                account_id=str(r.get("account") or ""),
                transfer_id=str(r.get("transfer_id") or ""),
                is_parent=bool(r.get("is_parent")),
            )
        )
    return rows


def parse_month(payload: Any) -> BudgetMonth:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise AppError("Actual ответил в неожиданном формате (месяц бюджета)")
    categories: list[BudgetCategory] = []
    for group in data.get("categoryGroups") or []:
        if not isinstance(group, dict) or group.get("hidden"):
            continue
        for c in group.get("categories") or []:
            if not isinstance(c, dict) or not c.get("id") or c.get("hidden"):
                continue
            categories.append(
                BudgetCategory(
                    id=str(c["id"]),
                    name=str(c.get("name") or c["id"]),
                    group=str(group.get("name") or ""),
                    budgeted=_int(c.get("budgeted")),
                    spent=_int(c.get("spent")),
                    balance=_int(c.get("balance")),
                    is_income=bool(c.get("is_income") or group.get("is_income")),
                    received=_int(c.get("received")),
                )
            )
    return BudgetMonth(
        month=str(data.get("month") or ""),
        income=_int(data.get("totalIncome")),
        spent=abs(_int(data.get("totalSpent"))),
        budgeted=_int(data.get("totalBudgeted")),
        to_budget=_int(data.get("toBudget")),
        categories=tuple(categories),
    )


def parse_month_category(payload: Any) -> BudgetCategory | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not data.get("id"):
        return None
    return BudgetCategory(
        id=str(data["id"]),
        name=str(data.get("name") or ""),
        group="",
        budgeted=_int(data.get("budgeted")),
        spent=_int(data.get("spent")),
        balance=_int(data.get("balance")),
        is_income=bool(data.get("is_income")),
    )


# ------------------------------------------------------------------- логика
def pick_account(accounts: Sequence[Account], wanted: str) -> Account | None:
    """По id или названию (без учёта регистра); пусто — первый открытый счёт бюджета."""
    open_accounts = [a for a in accounts if not a.closed]
    wanted = wanted.strip().casefold()
    if wanted:
        return next((a for a in open_accounts if a.id.casefold() == wanted or a.name.casefold() == wanted), None)
    return next((a for a in open_accounts if not a.offbudget), open_accounts[0] if open_accounts else None)


def month_start(today: date) -> date:
    return today.replace(day=1)


def shift_month(month: str, delta: int) -> str:
    """'2026-10', -1 -> '2026-09'."""
    year, mon = (int(x) for x in month.split("-"))
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


@dataclass(frozen=True, slots=True)
class MonthSummary:
    spent: int  # копейки, положительное число
    earned: int
    by_category: tuple[tuple[str, int], ...]  # (название, потрачено) по убыванию
    count: int


def summarize(txns: Sequence[Txn], categories: Sequence[Category], today: date) -> MonthSummary:
    """Итоги текущего месяца по операциям. Переводы между счетами расходом не считаются."""
    start = month_start(today).isoformat()
    names = {c.id: c.name for c in categories}
    spent = earned = count = 0
    per_cat: dict[str, int] = {}
    for t in txns:
        if t.date < start or t.date > today.isoformat() or t.is_transfer:
            continue
        count += 1
        if t.cents < 0:
            spent += -t.cents
            label = names.get(t.category_id, "Без категории")
            per_cat[label] = per_cat.get(label, 0) + -t.cents
        else:
            earned += t.cents
    top = tuple(sorted(per_cat.items(), key=lambda kv: kv[1], reverse=True))
    return MonthSummary(spent, earned, top, count)


def build_transaction_body(txn: Txn) -> dict[str, Any]:
    data: dict[str, Any] = {"date": txn.date, "amount": txn.cents}
    if txn.payee:
        data["payee_name"] = txn.payee
    if txn.category_id:
        data["category"] = txn.category_id
    if txn.notes:
        data["notes"] = txn.notes
    return {"learnCategories": False, "runTransfers": False, "transaction": data}


def find_mine(txns: Sequence[Txn], wanted: Txn) -> Txn | None:
    """Своя запись для «Отменить»: та же дата, сумма, категория и пометка UNI. Одинаковые траты взаимозаменяемы."""
    matches = [
        t
        for t in txns
        if t.id and t.mine and t.date == wanted.date and t.cents == wanted.cents and t.category_id == wanted.category_id
    ]
    return matches[-1] if matches else None


# ------------------------------------------------------------------- клиент
class ActualService:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        api_key: str,
        budget_id: str,
        *,
        account: str = "",
        encryption_password: str = "",
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._budget = budget_id
        self._account_name = account
        self._headers = {"x-api-key": api_key}
        if encryption_password:
            self._headers["budget-encryption-password"] = encryption_password
        self._account: Account | None = None

    @property
    def budget_id(self) -> str:
        return self._budget

    def _url(self, template: str, **values: str) -> str:
        return self._base + template.format(budget=self._budget, **values)

    async def _get(self, template: str, params: dict[str, str] | None = None, **values: str) -> Any:
        return await request(
            self._session, "GET", self._url(template, **values), headers=self._headers, params=params, timeout=TIMEOUT
        )

    async def accounts(self, *, balances: bool = False) -> list[Account]:
        params = {"include_balances": "true"} if balances else None
        return parse_accounts(await self._get(PATH_ACCOUNTS, params))

    async def categories(self) -> list[Category]:
        return [c for c in parse_categories(await self._get(PATH_CATEGORIES)) if not c.hidden]

    async def payees(self) -> dict[str, str]:
        return parse_payees(await self._get(PATH_PAYEES))

    async def account(self) -> Account:
        """Счёт по умолчанию из .env (находится один раз и запоминается)."""
        if self._account is None:
            found = pick_account(await self.accounts(), self._account_name)
            if found is None:
                hint = f" «{self._account_name}»" if self._account_name else ""
                raise AppError(f"в Actual не найден счёт{hint} — проверьте ACTUAL_ACCOUNT в .env")
            self._account = found
        return self._account

    async def transactions(self, account_id: str, since: date, until: date | None = None) -> list[Txn]:
        params = {"since_date": since.isoformat()}
        if until is not None:
            params["until_date"] = until.isoformat()
        return parse_transactions(await self._get(PATH_TRANSACTIONS, params, account=account_id))

    async def recent(self, accounts: Sequence[Account], since: date, limit: int = 10) -> list[Txn]:
        """Последние операции по нескольким счетам сразу (без половинок разбитых операций)."""
        batches = await asyncio.gather(*(self.transactions(a.id, since) for a in accounts))
        rows = [
            t if t.account_id else _with_account(t, account.id)
            for account, batch in zip(accounts, batches, strict=True)
            for t in batch
        ]
        rows.sort(key=lambda t: t.date, reverse=True)
        return rows[:limit]

    async def add(self, txn: Txn, account_id: str | None = None) -> None:
        target = account_id or (await self.account()).id
        url = self._url(PATH_TRANSACTIONS, account=target)
        await request(self._session, "POST", url, headers=self._headers, json_body=build_transaction_body(txn), timeout=TIMEOUT)

    async def delete(self, txn_id: str) -> None:
        await request(self._session, "DELETE", self._url(PATH_TRANSACTION, txn=txn_id), headers=self._headers, timeout=TIMEOUT)

    async def undo(self, txn: Txn, account_id: str) -> bool:
        """Удаляет только что записанную ботом операцию. False — не нашлась (уже удалили в Actual)."""
        day = date.fromisoformat(txn.date)
        found = find_mine(await self.transactions(account_id, day, day + timedelta(days=1)), txn)
        if found is None:
            return False
        await self.delete(found.id)
        return True

    async def budget_month(self, month: str) -> BudgetMonth:
        return parse_month(await self._get(PATH_MONTH, month=month))

    async def month_category(self, month: str, category_id: str) -> BudgetCategory | None:
        return parse_month_category(await self._get(PATH_MONTH_CATEGORY, month=month, category=category_id))

    async def month(self, today: date) -> MonthSummary:
        account = await self.account()
        txns = await self.transactions(account.id, month_start(today))
        return summarize(txns, await self.categories(), today)

    async def check(self) -> tuple[Account, int]:
        """(счёт для трат, число доступных категорий) — для команды /actual."""
        account = await self.account()
        return account, len(await self.categories())


def _with_account(txn: Txn, account_id: str) -> Txn:
    return replace(txn, account_id=account_id)
