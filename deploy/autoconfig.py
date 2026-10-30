#!/usr/bin/python3
"""Автонастройка приложений: находит контейнеры и достаёт их ключи из локальных конфигов. Только стандартная библиотека.

Запускает deploy/update.sh от root (бот сам эти файлы прочитать не может). Найденное пишется в
STATE_DIR/integrations.json — тот же файл, что и подключение из чата (🧩 Приложения). Ничего не перезаписывает:
приложение, ключ которого задан в .env или уже сохранён, пропускается.

  python3 autoconfig.py --env /opt/homebot/.env --state /var/lib/homebot [--ask] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FILE_NAME = "integrations.json"
KEY_RE = re.compile(r"<ApiKey>\s*([0-9a-fA-F]{20,64})\s*</ApiKey>")
PORT_RE = re.compile(r"<Port>\s*(\d{2,5})\s*</Port>")


@dataclass(frozen=True)
class App:
    service: str  # как в homebot/services/tiles.py (CONNECTORS)
    title: str
    images: tuple[str, ...]
    port: int
    url_field: str
    secret_env: str  # ключ в .env: если задан, приложение уже настроено
    extract: Callable[[dict[str, Any]], dict[str, str]]


# ------------------------------------------------------------------ извлечение ключей
def mounts(info: dict[str, Any], *destinations: str) -> list[Path]:
    return [Path(m["Source"]) for m in info.get("Mounts") or [] if m.get("Destination") in destinations and m.get("Source")]


def container_env(info: dict[str, Any]) -> dict[str, str]:
    out = {}
    for item in (info.get("Config") or {}).get("Env") or []:
        name, _, value = str(item).partition("=")
        out[name] = value
    return out


def arr_key(field: str) -> Callable[[dict[str, Any]], dict[str, str]]:
    """Radarr/Sonarr: <ApiKey> в /config/config.xml."""

    def extract(info: dict[str, Any]) -> dict[str, str]:
        for root in mounts(info, "/config"):
            try:
                text = (root / "config.xml").read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            match = KEY_RE.search(text)
            if match:
                return {field: match.group(1)}
        return {}

    return extract


def changedetection_key(info: dict[str, Any]) -> dict[str, str]:
    for root in mounts(info, "/datastore", "/config"):
        for path in sorted(root.glob("*.json")) if root.is_dir() else []:
            try:
                match = re.search(r'"api_access_token"\s*:\s*"([^"]+)"', path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if match:
                return {"changedetection_api_key": match.group(1)}
    return {}


def jellyfin_key(info: dict[str, Any]) -> dict[str, str]:
    """Первый API-ключ из базы Jellyfin (Панель → API-ключи). Базу открываем только на чтение."""
    for root in mounts(info, "/config"):
        for name in ("data/jellyfin.db", "data/data/jellyfin.db", "data/authentication.db", "data/data/authentication.db"):
            path = root / name
            if not path.is_file():
                continue
            try:
                with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
                    row = db.execute("SELECT AccessToken FROM ApiKeys ORDER BY DateCreated LIMIT 1").fetchone()
            except sqlite3.Error:
                continue
            if row and row[0]:
                return {"jellyfin_api_key": str(row[0])}
    return {}


def actual_key(info: dict[str, Any]) -> dict[str, str]:
    """actual-http-api: ключ — переменная API_KEY контейнера (Sync ID бюджета бот спросит в чате)."""
    key = container_env(info).get("API_KEY", "")
    return {"actual_api_key": key} if key else {}


APPS: tuple[App, ...] = (
    App("radarr", "Radarr", ("radarr",), 7878, "radarr_url", "RADARR_API_KEY", arr_key("radarr_api_key")),
    App("sonarr", "Sonarr", ("sonarr",), 8989, "sonarr_url", "SONARR_API_KEY", arr_key("sonarr_api_key")),
    App(
        "sites",
        "changedetection.io",
        ("changedetection",),
        5000,
        "changedetection_url",
        "CHANGEDETECTION_API_KEY",
        changedetection_key,
    ),
    App("jellyfin", "Jellyfin", ("jellyfin",), 8096, "jellyfin_url", "JELLYFIN_API_KEY", jellyfin_key),
    App("actual", "Actual Budget", ("actual-http-api", "actual_http_api"), 5007, "actual_url", "ACTUAL_API_KEY", actual_key),
)


# ------------------------------------------------------------------ поиск и адрес
def host_url(info: dict[str, Any], port: int) -> str:
    """Опубликованный порт приложения; без публикации (сеть хоста) — его обычный порт."""
    ports = (info.get("NetworkSettings") or {}).get("Ports") or {}
    published = None
    for spec, binds in ports.items():
        if not spec.endswith("/tcp") or not binds:
            continue
        host = next((b.get("HostPort") for b in binds if b.get("HostPort")), None)
        if host and spec == f"{port}/tcp":
            published = host
            break
        published = published or host
    return f"http://127.0.0.1:{published or port}"


def match(info: dict[str, Any]) -> App | None:
    if not (info.get("State") or {}).get("Running"):
        return None
    image = str((info.get("Config") or {}).get("Image", "")).lower()
    return next((app for app in APPS if any(p in image for p in app.images)), None)


def inspect_all() -> list[dict[str, Any]]:
    try:
        ids = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, timeout=20, check=True).stdout.split()
        if not ids:
            return []
        out = subprocess.run(["docker", "inspect", *ids], capture_output=True, text=True, timeout=30, check=True).stdout
        data = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return data if isinstance(data, list) else []


# ------------------------------------------------------------------ .env и integrations.json
def env_values(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def plan(containers: list[dict[str, Any]], env: dict[str, str], stored: dict[str, Any]) -> list[tuple[App, str, dict[str, str]]]:
    """Что добавить: (приложение, имя контейнера, значения). Заданное в .env или уже сохранённое пропускаем."""
    result = []
    seen: set[str] = set()
    for info in containers:
        app = match(info)
        if app is None or app.service in seen:
            continue
        seen.add(app.service)
        if env.get(app.secret_env) or app.service in stored:
            continue
        values = app.extract(info)
        if not values:
            continue
        values = {app.url_field: host_url(info, app.port), **values}
        result.append((app, str(info.get("Name", "")).lstrip("/"), values))
    return result


def save(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    try:
        user = pwd.getpwnam("homebot")
        os.chown(tmp, user.pw_uid, user.pw_gid)
    except (KeyError, PermissionError):
        pass
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--ask", action="store_true", help="спрашивать про каждое найденное приложение")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    path = args.state / FILE_NAME
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored = stored if isinstance(stored, dict) else {}
    except (OSError, ValueError):
        stored = {}
    found = plan(inspect_all(), env_values(args.env), stored)
    if not found:
        print("  · новых приложений с доступными ключами не нашёл")
        return 0
    added = []
    for app, container, values in found:
        if args.ask:
            answer = input(f"  ? Подключить {app.title} (контейнер {container}, {values[app.url_field]})? [Y/n] ").strip().lower()
            if answer in ("n", "no", "н", "нет"):
                continue
        stored[app.service] = values
        added.append(app.title)
        print(f"  ✓ {app.title}: ключ взят из контейнера {container}, адрес {values[app.url_field]}")
    if added and not args.dry_run:
        save(path, stored)
    return 0


if __name__ == "__main__":
    sys.exit(main())
