"""План приватных HTTPS-адресов приложений через Tailscale Serve."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from homebot.services.tiles import CONNECTOR_BY_SERVICE

BASE_PORT = 18443


@dataclass(frozen=True, slots=True)
class Exposure:
    service: str
    target: str
    listen_port: int
    public_url: str


def plan(services: list[str], internal_urls: dict[str, str], dns_name: str, base_port: int = BASE_PORT) -> list[Exposure]:
    """Создать стабильный план; принимает только loopback-цели, не LAN и не Docker DNS."""
    host = dns_name.strip().rstrip(".")
    if not host or base_port < 1024 or base_port + len(CONNECTOR_BY_SERVICE) > 65535:
        raise ValueError("некорректное DNS-имя или диапазон портов")
    out: list[Exposure] = []
    ordered = {c.service: i for i, c in enumerate(CONNECTOR_BY_SERVICE.values()) if c.url_field}
    for service in dict.fromkeys(services):
        connector = CONNECTOR_BY_SERVICE.get(service)
        if connector is None or not connector.url_field:
            raise ValueError(f"неизвестное приложение: {service}")
        raw = internal_urls.get(service) or f"http://127.0.0.1:{connector.port}"
        parts = urlsplit(raw)
        if parts.scheme != "http" or (parts.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(f"{service}: Tailscale Serve автоматически настраивается только для http://localhost")
        listen = base_port + ordered[service]
        target = f"http://127.0.0.1:{parts.port or connector.port}{parts.path.rstrip('/')}"
        out.append(Exposure(service, target, listen, f"https://{host}:{listen}"))
    return out
