"""AdGuard Home API: статистика и временное отключение защиты."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp

from homebot.services.apps_http import AppError, basic_auth, request


@dataclass(frozen=True, slots=True)
class AdGuardStats:
    protection: bool
    disabled_for_ms: int
    version: str
    queries: int
    blocked: int
    avg_ms: float
    top_blocked: tuple[tuple[str, int], ...]

    @property
    def blocked_percent(self) -> float:
        return self.blocked / self.queries * 100 if self.queries else 0.0


def _top(entries: Any, limit: int = 5) -> tuple[tuple[str, int], ...]:
    """`[{"ads.example.com": 120}, ...]` -> (("ads.example.com", 120), ...)."""
    pairs: list[tuple[str, int]] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            for key, value in entry.items():
                if isinstance(value, int | float):
                    pairs.append((str(key), int(value)))
    pairs.sort(key=lambda p: p[1], reverse=True)
    return tuple(pairs[:limit])


def parse_adguard(status: Any, stats: Any) -> AdGuardStats:
    if not isinstance(status, dict) or not isinstance(stats, dict):
        raise AppError("неожиданный ответ AdGuard Home")
    return AdGuardStats(
        protection=bool(status.get("protection_enabled")),
        disabled_for_ms=int(status.get("protection_disabled_duration") or 0),
        version=str(status.get("version") or ""),
        queries=int(stats.get("num_dns_queries") or 0),
        blocked=int(stats.get("num_blocked_filtering") or 0),
        avg_ms=float(stats.get("avg_processing_time") or 0) * 1000,
        top_blocked=_top(stats.get("top_blocked_domains")),
    )


class AdGuardService:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, username: str, password: str) -> None:
        self._session = session
        self._base = base_url.rstrip("/") + "/control"
        self._headers = {"Authorization": basic_auth(username, password)}

    async def stats(self) -> AdGuardStats:
        status = await request(self._session, "GET", f"{self._base}/status", headers=self._headers)
        stats = await request(self._session, "GET", f"{self._base}/stats", headers=self._headers)
        return parse_adguard(status, stats)

    async def set_protection(self, enabled: bool, *, minutes: int = 0) -> None:
        body: dict[str, Any] = {"enabled": enabled}
        if not enabled and minutes:
            body["duration"] = minutes * 60 * 1000  # миллисекунды
        await request(self._session, "POST", f"{self._base}/protection", headers=self._headers, json_body=body)
