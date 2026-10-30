"""Speedtest Tracker (API v1, Bearer-токен со способностями «Read Results» и «Run Speedtest»)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp

from homebot.services.apps_http import AppError, request


@dataclass(frozen=True, slots=True)
class SpeedResult:
    id: int
    created_at: datetime | None
    status: str
    download_mbps: float | None
    upload_mbps: float | None
    ping_ms: float | None
    healthy: bool | None = None
    server: str = ""
    isp: str = ""
    message: str = ""  # причина неудачи от Speedtest Tracker / Ookla, если есть

    @property
    def ok(self) -> bool:
        return self.status.lower() == "completed" and self.download_mbps is not None


def _mbps(item: dict[str, Any], key: str) -> float | None:
    bits = item.get(f"{key}_bits")
    if isinstance(bits, int | float) and not isinstance(bits, bool):
        return bits / 1_000_000
    raw = item.get(key)  # байт/с
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return raw * 8 / 1_000_000
    return None


def _time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_result(item: Any) -> SpeedResult:
    if not isinstance(item, dict) or "id" not in item:
        raise AppError("неожиданный формат результата Speedtest Tracker")
    details = item.get("data") if isinstance(item.get("data"), dict) else {}
    server = (details.get("server") or {}) if isinstance(details.get("server"), dict) else {}
    ping = item.get("ping")
    return SpeedResult(
        id=int(item["id"]),
        created_at=_time(item.get("created_at")),
        status=str(item.get("status") or ""),
        download_mbps=_mbps(item, "download"),
        upload_mbps=_mbps(item, "upload"),
        ping_ms=float(ping) if isinstance(ping, int | float) and not isinstance(ping, bool) else None,
        healthy=item.get("healthy") if isinstance(item.get("healthy"), bool) else None,
        server=str(server.get("name") or ""),
        isp=str(details.get("isp") or ""),
        message=_failure_message(item, details),
    )


def _failure_message(item: dict[str, Any], details: dict[str, Any]) -> str:
    """Текст ошибки замера: Speedtest Tracker кладёт его в data.message (или data.error, или message)."""
    for source in (details, item):
        for key in ("message", "error", "comments"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())[:200]
    return ""


def _unwrap(payload: Any) -> Any:
    """`/results/latest` может отдавать объект как есть или в обёртке {"data": {...}}."""
    if isinstance(payload, dict) and "id" not in payload and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


class SpeedtestService:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        self._session = session
        self._base = base_url.rstrip("/") + "/api/v1"
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def latest(self) -> SpeedResult | None:
        try:
            payload = await request(self._session, "GET", f"{self._base}/results/latest", headers=self._headers)
        except AppError as exc:
            if "404" in str(exc):
                return None  # замеров ещё не было
            if "500" in str(exc):
                return await self._latest_from_list(exc)  # /results/latest у Speedtest Tracker иногда падает на неудачном замере
            raise
        return parse_result(_unwrap(payload))

    async def _latest_from_list(self, original: AppError) -> SpeedResult | None:
        """Запасной путь: последний замер из общего списка. Если и он не отвечает — ошибка исходная."""
        try:
            payload = await request(
                self._session,
                "GET",
                f"{self._base}/results",
                headers=self._headers,
                params={"sort": "-created_at", "per_page": "5", "per.page": "5"},
                timeout=20,
            )
        except AppError:
            raise original from None
        items = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise original
        found = []
        for item in items:
            try:
                found.append(parse_result(item))
            except (AppError, ValueError, TypeError):
                continue
        if not found:
            return None
        return max(found, key=lambda r: r.id)

    async def history(self, days: int = 7, *, now: datetime | None = None) -> list[SpeedResult]:
        moment = now or datetime.now(UTC)
        since = (moment - timedelta(days=days)).strftime("%Y-%m-%d")
        url = f"{self._base}/results"
        try:
            payload = await request(
                self._session,
                "GET",
                url,
                headers=self._headers,
                params={"filter[start_at]": since, "sort": "created_at", "per_page": "500", "per.page": "500"},
                timeout=20,
            )
        except AppError as exc:
            if not any(code in str(exc) for code in ("500", "400", "422")):
                raise
            # некоторые версии Speedtest Tracker не понимают фильтры и сортировку: берём простой список и режем сами
            payload = await request(self._session, "GET", url, headers=self._headers, params={"per_page": "500"}, timeout=20)
        items = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise AppError("неожиданный формат списка результатов")
        results = []
        for item in items:
            try:
                results.append(parse_result(item))
            except (AppError, ValueError, TypeError):
                continue
        border = moment - timedelta(days=days)
        return sorted(
            (r for r in results if r.created_at is not None and r.created_at >= border),
            key=lambda r: r.created_at,  # type: ignore[arg-type, return-value]
        )

    async def run(self) -> None:
        await request(self._session, "POST", f"{self._base}/speedtests/run", headers=self._headers, timeout=20)
