#!/usr/bin/python3
"""Каталог приложений HomeBot: ставит контейнер по запросу из бота. Работает от root, только стандартная библиотека.

Ставится в /usr/local/sbin/homebot-catalog скриптом deploy/update.sh — только если в .env CATALOG_ENABLED=true —
и запускается systemd (homebot-catalog.service) при появлении /var/lib/homebot/catalog/request.json.

Безопасность:
* бот выбирает только имя из белого списка APPS ниже; образы, порты и каталоги зашиты здесь, а не в запросе;
* compose-файл пишется этим скриптом в root-каталог (/etc/homebot/catalog), бот его подменить не может;
* контейнеры получают метку uni.catalog=1 и проект uni-<имя>; чужие контейнеры скрипт не трогает.

Запрос: {"app": "jellyfin"}. Итог (result.json): {"app", "ok", "message", "url"}.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

STATE = Path("/var/lib/homebot")
REQUEST = STATE / "catalog" / "request.json"
RESULT = STATE / "catalog" / "result.json"
COMPOSE_DIR = Path("/etc/homebot/catalog")
ENV_FILE = Path("/opt/homebot/.env")
AUTOCONFIG = "/usr/local/sbin/homebot-autoconfig"  # root-копия deploy/autoconfig.py (код в /opt/homebot пишет бот)

# имя -> (образ, порт хоста:контейнера, тома относительно корня каталога, переменные окружения)
APPS: dict[str, dict] = {
    "jellyfin": {
        "image": "lscr.io/linuxserver/jellyfin:latest",
        "ports": ["8096:8096"],
        "volumes": ["{root}/jellyfin/config:/config", "{media}:/data/media"],
    },
    "radarr": {
        "image": "lscr.io/linuxserver/radarr:latest",
        "ports": ["7878:7878"],
        "volumes": ["{root}/radarr/config:/config", "{media}:/data/media", "{root}/downloads:/downloads"],
    },
    "sonarr": {
        "image": "lscr.io/linuxserver/sonarr:latest",
        "ports": ["8989:8989"],
        "volumes": ["{root}/sonarr/config:/config", "{media}:/data/media", "{root}/downloads:/downloads"],
    },
    "qbittorrent": {
        "image": "lscr.io/linuxserver/qbittorrent:latest",
        "ports": ["8080:8080", "6881:6881", "6881:6881/udp"],
        "volumes": ["{root}/qbittorrent/config:/config", "{root}/downloads:/downloads"],
        "env": {"WEBUI_PORT": "8080"},
    },
    "changedetection": {
        "image": "ghcr.io/dgtlmoon/changedetection.io:latest",
        "ports": ["5000:5000"],
        "volumes": ["{root}/changedetection:/datastore"],
        "linuxserver": False,
    },
}


def env_values(path: Path = ENV_FILE) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip()
    except OSError:
        pass
    return out


def compose_text(name: str, env: dict[str, str]) -> str:
    """docker-compose.yml для приложения из белого списка (JSON — допустимый YAML, экранировать ничего не надо)."""
    app = APPS[name]
    root = env.get("CATALOG_ROOT") or "/srv/uni"
    media = env.get("CATALOG_MEDIA") or f"{root}/media"
    environment = dict(app.get("env", {}))
    if app.get("linuxserver", True):
        environment |= {"PUID": env.get("CATALOG_PUID") or "1000", "PGID": env.get("CATALOG_PGID") or "1000"}
    environment["TZ"] = env.get("TIMEZONE") or "UTC"
    service = {
        "image": app["image"],
        "container_name": name,
        "restart": "unless-stopped",
        "ports": app["ports"],
        "volumes": [v.format(root=root, media=media) for v in app["volumes"]],
        "environment": environment,
        "labels": {"uni.catalog": "1"},
    }
    return json.dumps({"services": {name: service}}, ensure_ascii=False, indent=2)


def host_dirs(name: str, env: dict[str, str]) -> list[Path]:
    root = env.get("CATALOG_ROOT") or "/srv/uni"
    media = env.get("CATALOG_MEDIA") or f"{root}/media"
    return [Path(v.format(root=root, media=media).split(":", 1)[0]) for v in APPS[name]["volumes"]]


def write_result(data: dict) -> None:
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    tmp = RESULT.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    try:
        user = pwd.getpwnam("homebot")
        os.chown(tmp, user.pw_uid, user.pw_gid)
    except KeyError:
        pass
    os.replace(tmp, RESULT)


def install(name: str) -> dict:
    env = env_values()
    if env.get("CATALOG_ENABLED", "").lower() != "true":
        return {"ok": False, "message": "каталог выключен (CATALOG_ENABLED=false)"}
    if shutil.which("docker") is None:
        return {"ok": False, "message": "Docker не установлен"}
    exists = subprocess.run(["docker", "inspect", name], capture_output=True, timeout=30)
    if exists.returncode == 0:
        return {"ok": False, "message": f"контейнер {name} уже есть — подключите его в 🧩 Приложениях"}
    puid, pgid = int(env.get("CATALOG_PUID") or 1000), int(env.get("CATALOG_PGID") or 1000)
    for path in host_dirs(name, env):
        if not path.exists():  # существующие каталоги (например, медиатеку) не трогаем
            path.mkdir(parents=True)
            os.chown(path, puid, pgid)
    COMPOSE_DIR.mkdir(parents=True, exist_ok=True)
    compose = COMPOSE_DIR / f"{name}.yml"
    compose.write_text(compose_text(name, env), encoding="utf-8")
    run = subprocess.run(
        ["docker", "compose", "-p", f"uni-{name}", "-f", str(compose), "up", "-d", "--quiet-pull"],
        capture_output=True,
        text=True,
        timeout=1200,
    )
    if run.returncode != 0:
        tail = (run.stderr or run.stdout).strip().splitlines()[-3:]
        return {"ok": False, "message": "docker compose: " + " ".join(tail)[:300]}
    port = APPS[name]["ports"][0].split(":", 1)[0]
    found = autoconfigure()
    return {"ok": True, "message": "установлен", "url": f"http://127.0.0.1:{port}", "configured": found}


def autoconfigure(wait: int = 90, step: int = 5) -> bool:
    """Ключ нового приложения появляется в его конфиге через несколько секунд после запуска — ждём и забираем."""
    for _ in range(wait // step):
        time.sleep(step)
        run = subprocess.run([AUTOCONFIG, "--env", str(ENV_FILE), "--state", str(STATE)], capture_output=True, text=True, timeout=120)
        if "✓" in run.stdout:
            return True
    return False


def main() -> int:
    try:
        request = json.loads(REQUEST.read_text(encoding="utf-8"))
        name = str(request.get("app", ""))
    except (OSError, ValueError, AttributeError):
        name = ""
    REQUEST.unlink(missing_ok=True)
    if name not in APPS:
        write_result({"app": name, "ok": False, "message": "такого приложения нет в каталоге"})
        return 1
    try:
        result = install(name)
    except (OSError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "message": f"{type(exc).__name__}: {exc}"[:300]}
    write_result({"app": name, **result})
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
