"""Сеть: устройства Tailscale, внешний IP, текущая скорость интерфейсов."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import aiohttp
import psutil

from homebot.utils.proc import run_command

log = logging.getLogger(__name__)

_IP_URL = "https://api.ipify.org"


class TailscaleError(Exception):
    """Tailscale недоступен или вернул непонятный ответ. Текст безопасно показывать пользователю."""


@dataclass(frozen=True, slots=True)
class TailnetNode:
    name: str
    ip: str
    os: str
    online: bool
    last_seen: datetime | None = None
    exit_node: bool = False
    is_self: bool = False


@dataclass(frozen=True, slots=True)
class TailnetStatus:
    backend_state: str
    nodes: tuple[TailnetNode, ...]
    health: tuple[str, ...] = field(default_factory=tuple)

    @property
    def online_count(self) -> int:
        return sum(1 for n in self.nodes if n.online)


def _parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or raw.startswith("0001-"):
        return None  # Tailscale отдаёт «нулевую» дату для ни разу не виденных устройств
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _node(item: dict[str, Any], *, is_self: bool) -> TailnetNode:
    ips = item.get("TailscaleIPs") or []
    ip4 = next((ip for ip in ips if isinstance(ip, str) and "." in ip), ips[0] if ips else "")
    name = item.get("HostName") or str(item.get("DNSName", "")).split(".")[0] or "?"
    return TailnetNode(
        name=str(name),
        ip=str(ip4),
        os=str(item.get("OS") or ""),
        online=bool(item.get("Online")) or is_self,
        last_seen=_parse_time(item.get("LastSeen")),
        exit_node=bool(item.get("ExitNode")),
        is_self=is_self,
    )


def parse_tailscale_status(data: Any) -> TailnetStatus:
    """Разбирает вывод `tailscale status --json`. Бросает TailscaleError при неожиданном формате."""
    if not isinstance(data, dict) or "Self" not in data:
        raise TailscaleError("неожиданный формат ответа tailscale")
    nodes = [_node(data["Self"], is_self=True)]
    peers = data.get("Peer") or {}
    if isinstance(peers, dict):
        nodes += [_node(p, is_self=False) for p in peers.values() if isinstance(p, dict)]
    # Сначала своё устройство, затем онлайн, затем по имени.
    nodes.sort(key=lambda n: (not n.is_self, not n.online, n.name.lower()))
    health = tuple(str(h) for h in (data.get("Health") or []) if h)
    return TailnetStatus(str(data.get("BackendState") or "?"), tuple(nodes), health)


class NetworkService:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def tailscale(self) -> TailnetStatus:
        result = await run_command("tailscale", "status", "--json", timeout=10)
        if not result.ok:
            raise TailscaleError(result.problem)
        try:
            return parse_tailscale_status(json.loads(result.stdout))
        except ValueError as exc:  # JSONDecodeError
            raise TailscaleError("tailscale вернул некорректный JSON") from exc

    async def external_ip(self) -> str | None:
        try:
            async with self._session.get(_IP_URL, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                resp.raise_for_status()
                text = (await resp.text()).strip()
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.debug("External IP lookup failed: %s", exc)
            return None
        return text if 0 < len(text) <= 45 else None

    async def throughput(self, interval: float = 1.0) -> tuple[float, float, int, int]:
        """(приём Б/с, отдача Б/с, всего принято, всего отправлено) по всем интерфейсам."""
        first = await asyncio.to_thread(psutil.net_io_counters)
        await asyncio.sleep(interval)
        second = await asyncio.to_thread(psutil.net_io_counters)
        rx = (second.bytes_recv - first.bytes_recv) / interval
        tx = (second.bytes_sent - first.bytes_sent) / interval
        return max(0.0, rx), max(0.0, tx), second.bytes_recv, second.bytes_sent
