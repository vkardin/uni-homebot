#!/usr/bin/env python3
"""Подготовить или применить приватные HTTPS-прокси для доски приложений.

Без --apply только печатает план. Tailscale Funnel не используется.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from homebot.services.launcher import public_urls  # noqa: E402
from homebot.services.launcher_tailscale import BASE_PORT, plan  # noqa: E402
from homebot.services.tiles import CONNECTOR_BY_SERVICE  # noqa: E402


def read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.lstrip().startswith("#"):
            values[key.strip()] = value.strip().strip("\"'")
    return lines, values


def dns_name() -> str:
    result = subprocess.run(["tailscale", "status", "--json"], check=True, capture_output=True, text=True)
    return str(json.loads(result.stdout).get("Self", {}).get("DNSName", "")).rstrip(".")


def update_env(path: Path, lines: list[str], value: str) -> None:
    replacement = f"LAUNCHER_URLS={value}"
    done = False
    for i, line in enumerate(lines):
        if line.startswith("LAUNCHER_URLS="):
            lines[i], done = replacement, True
    if not done:
        lines.extend(["", replacement])
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(temporary, path.stat().st_mode & 0o777)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Приватные адреса приложений через Tailscale Serve")
    parser.add_argument("services", nargs="*", help="radarr sonarr jellyfin qbit adguard speedtest sites hass actual")
    parser.add_argument("--env", type=Path, default=Path("/opt/homebot/.env"))
    parser.add_argument("--host", help="полное MagicDNS-имя; обычно определяется автоматически")
    parser.add_argument("--base-port", type=int, default=BASE_PORT)
    parser.add_argument("--apply", action="store_true", help="применить Serve и записать LAUNCHER_URLS")
    args = parser.parse_args()
    lines, values = read_env(args.env)
    services = args.services
    if not services:
        services = [
            c.service for c in CONNECTOR_BY_SERVICE.values()
            if c.url_field and c.fields and all(values.get(f.name.upper(), "") for f in c.fields)
        ]
    if not services:
        raise SystemExit("Не найдены подключённые приложения. Передайте имена явно, например: radarr jellyfin")
    host = args.host or dns_name()
    internals = {s: values.get(CONNECTOR_BY_SERVICE[s].url_field.upper(), "") for s in services if s in CONNECTOR_BY_SERVICE}
    exposures = plan(services, internals, host, args.base_port)
    print("Приватный план Tailscale Serve (Funnel не используется):")
    for item in exposures:
        print(f"  {item.service:12} {item.public_url} -> {item.target}")
    if not args.apply:
        print("\nПроверка без изменений. Для применения повторите с --apply.")
        return 0
    configured = public_urls(values.get("LAUNCHER_URLS", ""))
    for item in exposures:
        subprocess.run(
            ["tailscale", "serve", "--bg", f"--https={item.listen_port}", item.target], check=True
        )
        configured[item.service] = item.public_url
    update_env(args.env, lines, ",".join(f"{key}={url}" for key, url in sorted(configured.items())))
    print(f"\nГотово. Перезапустите homebot; настройки записаны в {args.env}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
