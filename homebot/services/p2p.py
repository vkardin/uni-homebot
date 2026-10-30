"""💱 P2P-курс Binance: за сколько сейчас можно продать USDT (или другой актив) за гривну.

Ключи не нужны — берётся публичный список объявлений. Основной запрос — тот, которым пользуется сайт Binance P2P
(`.../c2c/v2/friendly/c2c/adv/search`), запасной — публичный `.../c2c/v1/public/c2c/agent/ad-list`. Разбор ответа
намеренно терпимый: поля могут лежать и в `adv`/`advertiser`, и плоско.

Цена «как есть» (лучшая) бывает случайной — объявление с крошечными лимитами или ненадёжный продавец. Поэтому кроме
лучшей считаем «типичную» — медиану первых нескольких надёжных объявлений: по ней и уведомляем.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp

from homebot.services.apps_http import AppError, request

SEARCH_URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
PUBLIC_LIST_URL = "https://www.binance.com/bapi/c2c/v1/public/c2c/agent/ad-list"
_HEADERS = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) UNI-bot"}

TOP = 5  # сколько первых надёжных объявлений идёт в «типичную» цену
MIN_FINISH_RATE = 0.90  # доля завершённых сделок за месяц
MIN_ORDERS = 30  # сделок за месяц
HISTORY_KEY = "p2p.history"
STATE_KEY = "p2p.state"  # {baseline, armed, summary_day} — состояние фоновой проверки
CHART_DAYS = 7
KEEP_SEC = 8 * 24 * 3600
POINT_GAP_SEC = 600  # в историю пишем не чаще раза в 10 минут


@dataclass(frozen=True, slots=True)
class P2PAd:
    price: float
    merchant: str = ""
    min_amount: float | None = None
    max_amount: float | None = None
    finish_rate: float | None = None  # 0..1
    orders: int | None = None
    methods: tuple[str, ...] = ()

    @property
    def reliable(self) -> bool:
        """Нет данных о продавце — считаем надёжным (не отбрасывать всё из-за формата ответа)."""
        if self.finish_rate is not None and self.finish_rate < MIN_FINISH_RATE:
            return False
        return not (self.orders is not None and self.orders < MIN_ORDERS)


@dataclass(frozen=True, slots=True)
class P2PQuote:
    at: float
    best: float  # лучшая цена среди надёжных объявлений
    typical: float  # медиана первых TOP надёжных
    ads: tuple[P2PAd, ...]  # первые надёжные (для показа)
    total: int  # сколько объявлений нашли всего


@dataclass(frozen=True, slots=True)
class P2PStats:
    low: float
    high: float
    average: float
    first: float
    last: float
    points: int

    @property
    def change_percent(self) -> float:
        return (self.last - self.first) / self.first * 100 if self.first else 0.0


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ".").strip())
        except ValueError:
            return None
    return None


def _first(source: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if source.get(key) not in (None, ""):
            return source[key]
    return None


def _methods(raw: Any) -> tuple[str, ...]:
    result: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, Mapping):
                name = _first(item, "tradeMethodName", "identifier", "name")
                if isinstance(name, str):
                    result.append(name)
    return tuple(dict.fromkeys(result))


def _items(payload: Any) -> list[Any]:
    """Список объявлений из ответа любой из форм: `data: [...]`, `data: {ads|list|items: [...]}` или просто список."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, Mapping):
        return []
    data = payload.get("data", payload)
    if isinstance(data, list):
        return data
    if isinstance(data, Mapping):
        for key in ("ads", "list", "items", "data", "advs"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def parse_ads(payload: Any) -> list[P2PAd]:
    """Объявления в порядке ответа; записи без цены пропускаются."""
    ads: list[P2PAd] = []
    for item in _items(payload):
        if not isinstance(item, Mapping):
            continue
        adv = item.get("adv") if isinstance(item.get("adv"), Mapping) else item
        who = item.get("advertiser") if isinstance(item.get("advertiser"), Mapping) else item
        price = _number(_first(adv, "price", "priceValue"))
        if price is None or price <= 0:
            continue
        rate = _number(_first(who, "monthFinishRate", "completionRate", "finishRate"))
        if rate is not None and rate > 1.5:  # проценты вместо долей
            rate /= 100
        orders = _number(_first(who, "monthOrderCount", "orderCount", "orders"))
        ads.append(
            P2PAd(
                price=price,
                merchant=str(_first(who, "nickName", "merchantName", "nickname") or ""),
                min_amount=_number(_first(adv, "minSingleTransAmount", "minAmount", "minLimit")),
                max_amount=_number(_first(adv, "dynamicMaxSingleTransAmount", "maxSingleTransAmount", "maxAmount", "maxLimit")),
                finish_rate=rate,
                orders=int(orders) if orders is not None else None,
                methods=_methods(_first(adv, "tradeMethods", "payMethods", "tradeMethodIdentifiers")),
            )
        )
    return ads


def make_quote(ads: Sequence[P2PAd], *, now: float | None = None, top: int = TOP, sell: bool = True) -> P2PQuote:
    """Лучшая и типичная цена. Для продажи актива лучше выше, для покупки — ниже."""
    if not ads:
        raise AppError("Binance P2P: объявлений нет — возможно, слишком строгие фильтры (сумма, способ оплаты)")
    good = [a for a in ads if a.reliable] or list(ads)
    ordered = sorted(good, key=lambda a: a.price, reverse=sell)
    head = ordered[:top]
    return P2PQuote(
        at=time.time() if now is None else now,
        best=ordered[0].price,
        typical=round(statistics.median(a.price for a in head), 2),
        ads=tuple(ordered[:3]),
        total=len(ads),
    )


class P2PService:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        fiat: str = "UAH",
        asset: str = "USDT",
        pay_types: Sequence[str] = (),
        amount: float = 0.0,
        rows: int = 20,
    ) -> None:
        self._session = session
        self.fiat = fiat.upper()
        self.asset = asset.upper()
        self._pay_types = tuple(pay_types)
        self._amount = amount
        self._rows = rows

    async def _search(self) -> list[P2PAd]:
        body: dict[str, Any] = {
            "fiat": self.fiat,
            "asset": self.asset,
            "tradeType": "SELL",  # «я продаю актив»: покупатели платят фиатом
            "page": 1,
            "rows": self._rows,
            "payTypes": list(self._pay_types),
            "publisherType": None,
            "countries": [],
            "proMerchantAds": False,
            "shieldMerchantAds": False,
            "filterType": "all",
        }
        if self._amount > 0:
            body["transAmount"] = f"{self._amount:g}"
        payload = await request(self._session, "POST", SEARCH_URL, headers=_HEADERS, json_body=body, timeout=15)
        if isinstance(payload, Mapping) and payload.get("code") not in (None, "000000"):
            raise AppError(f"Binance P2P: {payload.get('message') or payload.get('code')}")
        return parse_ads(payload)

    async def _public_list(self) -> list[P2PAd]:
        params: dict[str, Any] = {"fiat": self.fiat, "asset": self.asset, "tradeType": "SELL", "limit": min(self._rows, 20)}
        if self._pay_types:
            params["tradeMethodIdentifiers"] = ",".join(self._pay_types)
        return parse_ads(await request(self._session, "GET", PUBLIC_LIST_URL, headers=_HEADERS, params=params, timeout=15))

    async def quote(self) -> P2PQuote:
        """Основной запрос, при сбое или пустом ответе — запасной. Если не вышло ни там, ни там — исходная ошибка."""
        try:
            ads = await self._search()
            error: AppError | None = None
        except AppError as exc:
            ads, error = [], exc
        if not ads:
            try:
                ads = await self._public_list()
            except AppError as exc:
                error = error or exc
        if not ads and error is not None:
            raise error
        return make_quote(ads)


# ------------------------------------------------------------------ история и правила уведомлений
def add_point(history: Any, quote: P2PQuote) -> list[list[float]]:
    """Добавляет точку [время, лучшая, типичная], не чаще раза в POINT_GAP_SEC, и отрезает всё старше KEEP_SEC."""
    points = [p for p in history if isinstance(p, list) and len(p) == 3] if isinstance(history, list) else []
    if not points or quote.at - points[-1][0] >= POINT_GAP_SEC:
        points.append([quote.at, quote.best, quote.typical])
    return [p for p in points if quote.at - p[0] <= KEEP_SEC]


def stats(history: Any, *, now: float, hours: float = 24) -> P2PStats | None:
    if not isinstance(history, list):
        return None
    values = [(p[0], p[2]) for p in history if isinstance(p, list) and len(p) == 3 and now - p[0] <= hours * 3600]
    if not values:
        return None
    prices = [v for _, v in values]
    return P2PStats(min(prices), max(prices), sum(prices) / len(prices), prices[0], prices[-1], len(prices))


def change_percent(baseline: float | None, price: float) -> float:
    return (price - baseline) / baseline * 100 if baseline else 0.0


def crossed_target(target: float, price: float, armed: bool, hysteresis: float = 0.05) -> tuple[bool, bool]:
    """(сообщать ли сейчас, взведена ли цель дальше). Цель сообщается один раз при подъёме до неё и взводится снова,
    когда цена опустится ниже цели (с небольшим запасом против дребезга)."""
    if target <= 0:
        return False, True
    if price >= target:
        return armed, False
    if price < target - hysteresis:
        return False, True
    return False, armed


def chart_points(history: Any, *, now: float, days: float = CHART_DAYS) -> list[tuple[float, float, float]]:
    """(время, лучшая, типичная) за последние `days` суток — для графика."""
    if not isinstance(history, list):
        return []
    return [(p[0], p[1], p[2]) for p in history if isinstance(p, list) and len(p) == 3 and now - p[0] <= days * 86400]


def parse_price(text: str) -> float | None:
    """Цена из сообщения: «41.5», «41,5 грн», «₴ 41». Нечисловое и неразумное (≤0, >1 000 000) — None."""
    cleaned = text.replace(" ", " ").strip().lower()
    for junk in ("грн", "uah", "₴", "usd", "$", "usdt"):
        cleaned = cleaned.replace(junk, "")
    value = _number(cleaned.replace(" ", ""))
    if value is None or not 0 < value <= 1_000_000:  # NaN тоже не проходит сравнение
        return None
    return value
