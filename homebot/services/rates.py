"""Официальные курсы НБУ (без ключа)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

NBU_URL = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?json"


@dataclass(frozen=True, slots=True)
class Rate:
    code: str
    rate: float
    date: str


def parse_nbu(data: Any, codes: Sequence[str]) -> list[Rate]:
    """Из ответа НБУ берёт нужные валюты в порядке `codes`. Битые записи пропускает."""
    if not isinstance(data, list):
        raise ValueError("ожидался список курсов")
    found: dict[str, Rate] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        code = str(item.get("cc", "")).upper()
        value = item.get("rate")
        if code and isinstance(value, int | float) and not isinstance(value, bool):
            found[code] = Rate(code, float(value), str(item.get("exchangedate", "")))
    return [found[c.upper()] for c in codes if c.upper() in found]


async def fetch_rates(session: aiohttp.ClientSession, codes: Sequence[str]) -> list[Rate]:
    async with session.get(NBU_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)
    return parse_nbu(data, codes)
