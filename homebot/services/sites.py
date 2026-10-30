"""🔎 Сайты: слежка за ценами и изменениями страниц через changedetection.io (его REST API v1).

Ключ API — changedetection → Settings → API (deploy/update.sh подставляет его сам).

Два режима слежки:
* «price» — процессор restock_diff («цена товара»): снимок страницы выглядит как `In Stock: True - Price: 129.99`;
* «page» — обычный text_json_diff: любые изменения текста страницы.
Цены и наличие бот берёт из этих снимков, поэтому он не зависит от того, какие поля API отдаёт конкретная версия.
"""

from __future__ import annotations

import asyncio
import difflib
import re
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from homebot.services.apps_http import AppError, request

PRICE_PROCESSOR = "restock_diff"
PAGE_PROCESSOR = "text_json_diff"
STATE_KEY = "sites"  # {seen: {uuid: last_changed}, errors: {uuid: текст}} — чтобы не повторяться после перезапуска
KEY_TARGETS = "sites_targets"  # {uuid: {price: цель, armed: можно ли сообщать}} — отдельный ключ: меняется из обработчиков
MAX_URLS = 10  # столько ссылок за раз
HISTORY_LIMIT = 150  # столько последних снимков берём для графика и «минимума за всё время»
BROWSER_BACKEND = "html_webdriver"  # Chrome/Playwright в changedetection

SITES_SHOWN = 10  # столько слежек показываем кнопками
MAX_SNAPSHOT_CHARS = 200_000
INTERVAL_CHOICES = (0, 15, 60, 180, 720, 1440)  # минуты; 0 — как в настройках changedetection

_PRICE_SNAPSHOT_RE = re.compile(r"In Stock:\s*(True|False|None)\b.*?Price:\s*([0-9]+(?:[.,][0-9]+)?)?", re.IGNORECASE | re.DOTALL)
_URL_RE = re.compile(r"(https?://[^\s<>\"']+)", re.IGNORECASE)
_BARE_RE = re.compile(r"^(?:www\.)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:[/?#]\S*)?$", re.IGNORECASE)
_BLOCKED_RE = re.compile(r"403|429|forbidden|captcha|cloudflare|blocked|too many|access denied", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Site:
    uuid: str
    url: str
    title: str = ""
    mode: str = ""  # price / page; пусто — режим неизвестен (в коротком списке API его нет)
    paused: bool = False
    last_checked: int = 0
    last_changed: int = 0  # 0 — изменений ещё не было (снимок пока один)
    error: str = ""
    interval_min: int = 0  # 0 — по умолчанию
    price: float | None = None
    last_price: float | None = None
    in_stock: bool | None = None
    browser: bool = False  # страницу открывает браузер (а не простой запрос)

    @property
    def name(self) -> str:
        return self.title.strip() or domain(self.url)

    @property
    def is_price(self) -> bool:
        return self.mode == "price"

    @property
    def blocked(self) -> bool:
        """Сайт не пускает обычные запросы (нужен браузерный режим в changedetection)."""
        return bool(self.error) and bool(_BLOCKED_RE.search(self.error))


@dataclass(frozen=True, slots=True)
class PriceStats:
    points: int
    lowest: float
    lowest_at: int
    highest: float
    highest_at: int
    first: float


@dataclass(frozen=True, slots=True)
class Change:
    kind: str  # price / page
    old_price: float | None = None
    new_price: float | None = None
    old_stock: bool | None = None
    new_stock: bool | None = None
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


# ------------------------------------------------------------------ чистая логика
def domain(url: str) -> str:
    host = urlsplit(url).hostname or url
    return host.removeprefix("www.")


def normalize_url(text: str) -> str | None:
    """Ссылка из сообщения: `https://…`, либо голый адрес `example.com/item/…` (тогда добавляем https://)."""
    text = (text or "").strip()
    found = _URL_RE.search(text)
    if found:
        url = found.group(1).rstrip(").,;")
    elif _BARE_RE.match(text):
        url = "https://" + text
    else:
        return None
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or "." not in parts.hostname or len(url) > 2000:
        return None
    return url


def extract_urls(text: str, limit: int = MAX_URLS) -> list[str]:
    """Все ссылки из сообщения (по одной на слово/строку), без повторов, не больше `limit`."""
    found: list[str] = []
    for token in re.split(r"\s+", (text or "").strip()):
        url = normalize_url(token)
        if url and url not in found:
            found.append(url)
    return found[:limit]


def price_series(snapshots: list[tuple[int, str]]) -> list[tuple[int, float]]:
    """[(время, цена)] по снимкам слежки за ценой; снимки без цены и обычные страницы пропускаются."""
    series: list[tuple[int, float]] = []
    for stamp, text in sorted(snapshots):
        parsed = parse_snapshot(text)
        if parsed is not None and parsed[1] is not None:
            series.append((stamp, parsed[1]))
    return series


def price_stats(series: list[tuple[int, float]]) -> PriceStats | None:
    if not series:
        return None
    low = min(series, key=lambda p: (p[1], -p[0]))  # при равенстве — самая свежая дата
    high = max(series, key=lambda p: (p[1], -p[0]))
    return PriceStats(len(series), low[1], low[0], high[1], high[0], series[0][1])


def check_target(entry: object, price: float | None) -> tuple[bool, dict | None]:
    """Цель по цене: (достигнута ли сейчас — сообщить, обновлённая запись).

    Сообщаем один раз, когда цена опустилась до цели; снова — только после того, как она поднялась выше."""
    if not isinstance(entry, dict) or not isinstance(entry.get("price"), int | float):
        return False, None
    if price is None:
        return False, entry
    armed = bool(entry.get("armed", True))
    if price <= entry["price"]:
        return armed, {**entry, "armed": False}
    return False, {**entry, "armed": True}


def parse_snapshot(text: str) -> tuple[bool | None, float | None] | None:
    """`In Stock: True - Price: 129.99` → (в наличии, цена). Для обычной страницы — None."""
    match = _PRICE_SNAPSHOT_RE.search(text[:500])
    if match is None:
        return None
    stock = {"true": True, "false": False}.get(match.group(1).lower())
    price = float(match.group(2).replace(",", ".")) if match.group(2) else None
    return stock, price


def change_percent(old: float, new: float) -> float:
    return (new - old) / old * 100 if old else 0.0


def _lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def diff_lines(old: str, new: str, *, limit: int = 6, width: int = 160) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Что добавилось и что пропало (по строкам, без повторов), не больше `limit` строк каждого вида."""
    before, after = _lines(old[:MAX_SNAPSHOT_CHARS]), _lines(new[:MAX_SNAPSHOT_CHARS])
    added: list[str] = []
    removed: list[str] = []
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed.extend(before[i1:i2])
        if tag in ("replace", "insert"):
            added.extend(after[j1:j2])

    def clip(items: list[str]) -> tuple[str, ...]:
        unique = list(dict.fromkeys(item[:width] + ("…" if len(item) > width else "") for item in items))
        return tuple(unique[:limit])

    return clip(added), clip(removed)


def describe_change(old: str, new: str) -> Change:
    first, second = parse_snapshot(old), parse_snapshot(new)
    if first is not None and second is not None:
        return Change("price", old_price=first[1], new_price=second[1], old_stock=first[0], new_stock=second[0])
    added, removed = diff_lines(old, new)
    return Change("page", added=added, removed=removed)


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _interval_minutes(raw: dict[str, Any]) -> int:
    if raw.get("time_between_check_use_default", True):
        return 0
    spec = raw.get("time_between_check")
    if not isinstance(spec, dict):
        return 0
    weights = {"weeks": 10080, "days": 1440, "hours": 60, "minutes": 1, "seconds": 0}
    return int(sum(_int(spec.get(key)) * weight for key, weight in weights.items()))


def parse_site(uuid: str, raw: dict[str, Any]) -> Site:
    restock = raw.get("restock") if isinstance(raw.get("restock"), dict) else {}
    processor = raw.get("processor")
    error = raw.get("last_error")
    stock = restock.get("in_stock")
    backend = str(raw.get("fetch_backend") or "")
    return Site(
        uuid=uuid,
        url=str(raw.get("url") or ""),
        title=str(raw.get("title") or raw.get("page_title") or ""),
        mode="price" if processor == PRICE_PROCESSOR else "page" if processor else "",
        paused=bool(raw.get("paused")),
        last_checked=_int(raw.get("last_checked")),
        last_changed=_int(raw.get("last_changed")),
        error=" ".join(error.split())[:200] if isinstance(error, str) else "",
        interval_min=_interval_minutes(raw),
        price=_num(restock.get("price")),
        last_price=_num(restock.get("last_price")),
        in_stock=stock if isinstance(stock, bool) else None,
        browser=backend == BROWSER_BACKEND or backend.startswith("extra_browser"),
    )


def interval_body(minutes: int) -> dict[str, Any]:
    """Тело PUT для частоты проверки: 0 — вернуть значение по умолчанию."""
    if minutes <= 0:
        return {"time_between_check_use_default": True}
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    return {
        "time_between_check_use_default": False,
        "time_between_check": {"weeks": 0, "days": days, "hours": hours, "minutes": mins, "seconds": 0},
    }


# ----------------------------------------------------------------------- клиент
class SitesService:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str) -> None:
        self._session = session
        self._base = base_url.rstrip("/") + "/api/v1"
        self._headers = {"x-api-key": api_key}
        self._series: dict[str, tuple[tuple[int, ...], list[tuple[int, float]]]] = {}

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        return await request(self._session, method, self._base + path, headers=self._headers, timeout=20, **kwargs)

    async def brief(self) -> list[Site]:
        """Короткий список (быстро): без режима и цены."""
        data = await self._call("GET", "/watch")
        if not isinstance(data, dict):
            raise AppError("changedetection вернул неожиданный ответ")
        return [parse_site(uuid, raw) for uuid, raw in data.items() if isinstance(raw, dict)]

    async def get(self, uuid: str) -> Site:
        raw = await self._call("GET", f"/watch/{uuid}")
        if not isinstance(raw, dict):
            raise AppError("changedetection вернул неожиданный ответ")
        return parse_site(uuid, {**raw, "uuid": uuid})

    async def details(self, uuid: str) -> Site:
        """Полная карточка; цену и наличие, если API их не отдал, достаёт из снимков страницы."""
        site = await self.get(uuid)
        if site.is_price and site.price is None:
            try:
                pair = await self.last_two(uuid, allow_single=True)
            except AppError:
                pair = None
            if pair is not None:
                old, new = pair
                latest = parse_snapshot(new)
                previous = parse_snapshot(old) if old else None
                if latest is not None:
                    site = replace(site, in_stock=latest[0], price=latest[1], last_price=previous[1] if previous else None)
        return site

    async def sites(self, limit: int = SITES_SHOWN) -> tuple[list[Site], int]:
        """Слежки для экрана: полные карточки первых `limit` и общее число."""
        brief = await self.brief()
        brief.sort(key=lambda s: (s.paused, -s.last_changed, s.name.lower()))
        gate = asyncio.Semaphore(5)

        async def one(site: Site) -> Site:
            async with gate:
                try:
                    return await self.details(site.uuid)
                except AppError:
                    return site

        shown = await asyncio.gather(*(one(s) for s in brief[:limit]))
        return list(shown), len(brief)

    async def add(self, url: str, *, price: bool, browser: bool = False) -> str:
        body: dict[str, Any] = {"url": url, "processor": PRICE_PROCESSOR if price else PAGE_PROCESSOR}
        if browser:
            body["fetch_backend"] = BROWSER_BACKEND
        data = await self._call("POST", "/watch", json_body=body)
        uuid = data.get("uuid") if isinstance(data, dict) else None
        if isinstance(uuid, str) and uuid:
            return uuid
        for site in await self.brief():  # старые версии отвечают просто «OK» — ищем по ссылке
            if site.url == url:
                return site.uuid
        raise AppError("changedetection принял ссылку, но слежки в списке не видно")

    async def find(self, url: str) -> Site | None:
        key = url.rstrip("/").lower()
        return next((s for s in await self.brief() if s.url.rstrip("/").lower() == key), None)

    async def recheck(self, uuid: str) -> None:
        await self._call("GET", f"/watch/{uuid}", params={"recheck": "1"}, parse_json=False)

    async def recheck_all(self) -> None:
        await self._call("GET", "/watch", params={"recheck_all": "1"}, parse_json=False)

    async def set_paused(self, uuid: str, paused: bool) -> None:
        await self._call("GET", f"/watch/{uuid}", params={"paused": "paused" if paused else "unpaused"}, parse_json=False)

    async def set_interval(self, uuid: str, minutes: int) -> None:
        await self._call("PUT", f"/watch/{uuid}", json_body=interval_body(minutes), parse_json=False)

    async def set_browser(self, uuid: str, on: bool) -> None:
        await self._call("PUT", f"/watch/{uuid}", json_body={"fetch_backend": BROWSER_BACKEND if on else "system"}, parse_json=False)

    async def price_history(self, uuid: str, limit: int = HISTORY_LIMIT) -> list[tuple[int, float]]:
        """Цены по всем сохранённым снимкам (последние `limit`). Результат кэшируется, пока не появился новый снимок."""
        stamps = tuple((await self.history(uuid))[-limit:])
        cached = self._series.get(uuid)
        if cached is not None and cached[0] == stamps:
            return cached[1]
        gate = asyncio.Semaphore(5)

        async def one(stamp: int) -> tuple[int, str]:
            async with gate:
                return stamp, await self.snapshot(uuid, stamp)

        series = price_series(list(await asyncio.gather(*(one(s) for s in stamps))))
        self._series[uuid] = (stamps, series)
        return series

    async def delete(self, uuid: str) -> None:
        await self._call("DELETE", f"/watch/{uuid}", parse_json=False)

    async def history(self, uuid: str) -> list[int]:
        data = await self._call("GET", f"/watch/{uuid}/history")
        if not isinstance(data, dict):
            return []
        return sorted(int(key) for key in data if str(key).isdigit())

    async def snapshot(self, uuid: str, timestamp: int) -> str:
        raw = await self._call("GET", f"/watch/{uuid}/history/{timestamp}", parse_json=False)
        return raw[:MAX_SNAPSHOT_CHARS].decode("utf-8", errors="replace") if isinstance(raw, bytes) else ""

    async def last_two(self, uuid: str, *, allow_single: bool = False) -> tuple[str, str] | None:
        """(предыдущий снимок, последний). Снимок один — None (или ("", он), если `allow_single`)."""
        stamps = await self.history(uuid)
        if not stamps or (len(stamps) < 2 and not allow_single):
            return None
        new = await self.snapshot(uuid, stamps[-1])
        old = await self.snapshot(uuid, stamps[-2]) if len(stamps) >= 2 else ""
        return old, new

    async def check(self) -> int:
        """Для /doctor: доступен ли changedetection и подходит ли ключ; возвращает число слежек."""
        return len(await self.brief())


async def set_target(store, uuid: str, price: float | None, *, current: float | None = None) -> bool:
    """Задаёт (или убирает при None) цель по цене. Возвращает True, если цена уже ниже цели (сообщим при следующем снижении)."""
    targets = dict(store.get(KEY_TARGETS) or {})
    already_below = False
    if price is None:
        targets.pop(uuid, None)
    else:
        already_below = current is not None and current <= price
        targets[uuid] = {"price": float(price), "armed": not already_below}
    await store.set(KEY_TARGETS, targets)
    return already_below


def target_of(store, uuid: str) -> float | None:
    targets = store.get(KEY_TARGETS)
    entry = targets.get(uuid) if isinstance(targets, dict) else None
    return float(entry["price"]) if isinstance(entry, dict) and isinstance(entry.get("price"), int | float) else None
