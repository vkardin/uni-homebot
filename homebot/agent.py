"""🛰 Агент UNI для других машин: отдаёт главному боту метрики и принимает копии бэкапов.

Запуск на второй машине (нужны только psutil и aiohttp из requirements.txt):

    UNI_AGENT_TOKEN=длинный-секрет python -m homebot.agent --port 8099 --data /var/lib/uni-agent

Слушает все адреса, но рассчитан на сеть Tailscale: открывать порт в интернет не нужно. Каждый запрос — с заголовком
`Authorization: Bearer <токен>`. Агент ничего не меняет на машине: только читает метрики и складывает присланные
бэкапы в свою папку (последние N штук).
"""

from __future__ import annotations

import argparse
import hmac
import logging
import os
import re
import socket
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from homebot import __version__

log = logging.getLogger("homebot.agent")
MAX_BACKUP = 200 * 1024 * 1024
_NAME_RE = re.compile(r"^homebot-\d{8}-\d{6}\.tar\.gz$")


def metrics() -> dict[str, Any]:
    import psutil

    disks = []
    for part in psutil.disk_partitions(all=False):
        if part.fstype in {"squashfs", "tmpfs", "overlay"} or part.mountpoint.startswith(("/snap", "/boot/efi")):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append({"mount": part.mountpoint, "percent": usage.percent, "free": usage.free})
    temps: list[float] = []
    try:
        for entries in (psutil.sensors_temperatures() or {}).values():
            temps += [e.current for e in entries if e.current]
    except (AttributeError, OSError):
        pass
    return {
        "host": socket.gethostname(),
        "version": __version__,
        "cpu": psutil.cpu_percent(interval=0.3),
        "ram": psutil.virtual_memory().percent,
        "disk": max((d["percent"] for d in disks), default=None),
        "disks": disks,
        "temp": max(temps, default=None),
        "uptime": int(time.time() - psutil.boot_time()),
        "load": os.getloadavg()[0] if hasattr(os, "getloadavg") else None,
    }


def _prune(data_dir: Path, keep: int) -> None:
    for old in sorted(data_dir.glob("homebot-*.tar.gz"))[:-keep]:
        old.unlink(missing_ok=True)


def _listing(data_dir: Path) -> list[dict[str, Any]]:
    return [{"name": p.name, "size": p.stat().st_size} for p in sorted(data_dir.glob("homebot-*.tar.gz"))]


def build_app(token: str, data_dir: Path, keep: int = 5) -> web.Application:
    data_dir.mkdir(parents=True, exist_ok=True)

    @web.middleware
    async def auth(request: web.Request, handler: Any) -> web.StreamResponse:
        got = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token or not hmac.compare_digest(got.encode(), token.encode()):
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    async def get_metrics(request: web.Request) -> web.Response:
        return web.json_response(metrics())

    async def put_backup(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if not _NAME_RE.match(name):
            return web.json_response({"error": "bad name"}, status=400)
        tmp, size = data_dir / (name + ".part"), 0
        with tmp.open("wb") as fh:
            async for chunk in request.content.iter_chunked(65536):
                size += len(chunk)
                if size > MAX_BACKUP:
                    fh.close()
                    tmp.unlink(missing_ok=True)
                    return web.json_response({"error": "too big"}, status=413)
                fh.write(chunk)
        tmp.replace(data_dir / name)
        _prune(data_dir, keep)
        return web.json_response({"ok": True, "size": size})

    async def list_backups(request: web.Request) -> web.Response:
        return web.json_response({"items": _listing(data_dir)})

    app = web.Application(middlewares=[auth], client_max_size=MAX_BACKUP)
    app.router.add_get("/metrics", get_metrics)
    app.router.add_get("/backups", list_backups)
    app.router.add_put("/backups/{name}", put_backup)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m homebot.agent", description="Агент UNI")
    parser.add_argument("--host", default=os.environ.get("UNI_AGENT_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("UNI_AGENT_PORT", "8099")))
    parser.add_argument("--data", default=os.environ.get("UNI_AGENT_DATA", "agent-data"))
    parser.add_argument("--keep", type=int, default=5)
    args = parser.parse_args(argv)
    token = os.environ.get("UNI_AGENT_TOKEN", "")
    if len(token) < 16:
        print("Задайте UNI_AGENT_TOKEN (не короче 16 символов): его же вы впишете в панели главного бота.")
        return 2
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    web.run_app(build_app(token, Path(args.data), args.keep), host=args.host, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
