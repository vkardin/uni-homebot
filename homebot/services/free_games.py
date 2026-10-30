"""Бесплатные раздачи игр: Epic Games Store (официальный публичный JSON) и Steam/GOG (gamerpower.com).

Разбор — чистые функции (проверяются на образцах ответов), сетевой слой — `GamesService`.
Ответы чужих сайтов читаем терпимо: непонятная запись пропускается, а не роняет весь список.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiohttp

from homebot.services.apps_http import AppError, request

log = logging.getLogger(__name__)

EPIC_URL = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
EPIC_STORE = "https://store.epicgames.com/ru/p/"
GAMERPOWER_URL = "https://www.gamerpower.com/api/giveaways"
_IMAGE_PREFERENCE = ("OfferImageWide", "Thumbnail", "OfferImageTall", "DieselStoreFrontWide")
_TITLE_SUFFIX_RE = re.compile(r"\s*\((?:steam|gog|epic games|itch\.io|ubisoft|drm-free)[^)]*\)\s*giveaway\s*$", re.IGNORECASE)
_STORES = (("steam", "Steam"), ("gog", "GOG"), ("itch", "itch.io"), ("ubisoft", "Ubisoft Connect"))


@dataclass(frozen=True, slots=True)
class FreeGame:
    key: str  # уникален для конкретной раздачи (игра может раздаваться и повторно)
    store: str
    title: str
    url: str
    image: str = ""
    worth: str = ""  # «обычная цена», готовой строкой; пусто — неизвестна
    start: datetime | None = None
    end: datetime | None = None

    def is_active(self, now: datetime) -> bool:
        return (self.start is None or self.start <= now) and (self.end is None or now < self.end)


def _date(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw or raw.upper() == "N/A":
        return None
    text = raw.strip().replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _slug(element: dict[str, Any]) -> str:
    for source in (element.get("offerMappings"), (element.get("catalogNs") or {}).get("mappings")):
        for mapping in source or ():
            if isinstance(mapping, dict) and mapping.get("pageSlug"):
                return str(mapping["pageSlug"])
    for field in ("productSlug", "urlSlug"):
        value = element.get(field)
        if isinstance(value, str) and value and value != "[]":
            return value.removesuffix("/home")
    return ""


def _epic_image(element: dict[str, Any]) -> str:
    images = {i.get("type"): i.get("url") for i in element.get("keyImages") or () if isinstance(i, dict)}
    for kind in _IMAGE_PREFERENCE:
        url = images.get(kind)
        if isinstance(url, str) and url.startswith("http"):
            return url
    return ""


def _offers(block: Any) -> list[dict[str, Any]]:
    """Вложенный список `[{promotionalOffers: [...]}]` → плоский список предложений."""
    result: list[dict[str, Any]] = []
    for group in block or ():
        for offer in (group or {}).get("promotionalOffers") or ():
            if isinstance(offer, dict):
                result.append(offer)
    return result


def _is_free_offer(offer: dict[str, Any]) -> bool:
    setting = offer.get("discountSetting")
    return isinstance(setting, dict) and setting.get("discountPercentage") == 0  # 0 % к оплате = бесплатно


def _price(element: dict[str, Any]) -> tuple[int, str]:
    total = (element.get("price") or {}).get("totalPrice") or {}
    original = total.get("originalPrice")
    fmt = (total.get("fmtPrice") or {}).get("originalPrice")
    return (original if isinstance(original, int) else 0), (fmt if isinstance(fmt, str) else "")


def parse_epic(data: Any, now: datetime) -> list[FreeGame]:
    """Раздачи, действующие СЕЙЧАС. Будущие (`upcomingPromotionalOffers`) бот не объявляет."""
    try:
        elements = data["data"]["Catalog"]["searchStore"]["elements"]
    except (KeyError, TypeError) as exc:
        raise AppError("Epic ответил в неожиданном формате") from exc
    games: list[FreeGame] = []
    for element in elements or ():
        if not isinstance(element, dict) or not element.get("id"):
            continue
        original, shown = _price(element)
        if original <= 0:  # игра и так всегда бесплатна — не раздача
            continue
        slug = _slug(element)
        for offer in _offers((element.get("promotions") or {}).get("promotionalOffers")):
            start, end = _date(offer.get("startDate")), _date(offer.get("endDate"))
            game = FreeGame(
                key=f"epic:{element['id']}:{offer.get('endDate', '')}",
                store="Epic Games Store",
                title=str(element.get("title") or "?"),
                url=f"{EPIC_STORE}{slug}" if slug else "https://store.epicgames.com/ru/free-games",
                image=_epic_image(element),
                worth=shown,
                start=start,
                end=end,
            )
            if _is_free_offer(offer) and game.is_active(now):
                games.append(game)
                break
    return games


def _store_of(platforms: str) -> str | None:
    lowered = platforms.lower()
    if "epic" in lowered:
        return None  # Epic берём из официального источника
    for needle, name in _STORES:
        if needle in lowered:
            return name
    return None


def parse_gamerpower(data: Any, now: datetime) -> list[FreeGame]:
    if not isinstance(data, list):
        return []  # при отсутствии раздач сервис отвечает объектом с кодом ошибки — это не сбой
    games: list[FreeGame] = []
    for item in data:
        if not isinstance(item, dict) or str(item.get("type", "")).lower() != "game":
            continue
        if str(item.get("status", "Active")).lower() != "active":
            continue
        store = _store_of(str(item.get("platforms") or ""))
        title = _TITLE_SUFFIX_RE.sub("", str(item.get("title") or "")).strip()
        url = str(item.get("open_giveaway_url") or item.get("gamerpower_url") or "")
        if store is None or not title or not url.startswith("http"):
            continue
        worth = str(item.get("worth") or "")
        game = FreeGame(
            key=f"gp:{item.get('id')}",
            store=store,
            title=title,
            url=url,
            image=str(item.get("image") or item.get("thumbnail") or ""),
            worth=worth if worth.upper() != "N/A" else "",
            start=_date(item.get("published_date")),
            end=_date(item.get("end_date")),
        )
        if game.is_active(now):
            games.append(game)
    return games


class GamesService:
    def __init__(self, session: aiohttp.ClientSession, *, country: str = "UA", extra_sources: bool = True) -> None:
        self._session = session
        self._country = country.upper()
        self._extra = extra_sources

    async def _epic(self, now: datetime) -> list[FreeGame]:
        params = {"locale": "ru", "country": self._country, "allowCountries": self._country}
        return parse_epic(await request(self._session, "GET", EPIC_URL, params=params, timeout=20), now)

    async def _gamerpower(self, now: datetime) -> list[FreeGame]:
        params = {"platform": "steam.gog", "type": "game"}
        return parse_gamerpower(await request(self._session, "GET", GAMERPOWER_URL, params=params, timeout=20), now)

    async def fetch(self, now: datetime | None = None) -> list[FreeGame]:
        """Все действующие раздачи. Ошибка одного источника не мешает другому; если упали все — AppError."""
        moment = now or datetime.now(UTC)
        jobs: list[Any] = [self._epic(moment)]
        if self._extra:
            jobs.append(self._gamerpower(moment))
        results = await asyncio.gather(*jobs, return_exceptions=True)
        games: list[FreeGame] = []
        errors: list[str] = []
        for outcome in results:
            if isinstance(outcome, AppError):
                errors.append(str(outcome))
            elif isinstance(outcome, BaseException):
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
                errors.append(type(outcome).__name__)
            else:
                games.extend(outcome)
        if errors and len(errors) == len(results):
            raise AppError("; ".join(errors))
        for error in errors:
            log.warning("Free games: one source failed: %s", error)
        return games
