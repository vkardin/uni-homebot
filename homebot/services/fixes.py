"""🔧 Ремонт найденного самопроверкой: общее для чата (/doctor) и панели.

* `.env` — убрать ключи функций, которых в UNI больше нет (с резервной копией);
* контейнеры — перезапуск с зависимостями (services/autofix.py);
* qBittorrent — новые логин и пароль: сначала пробный вход, потом запись (с резервной копией .env).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from homebot.config import Settings
from homebot.services import connect, envfile
from homebot.services.apps import Apps
from homebot.services.envcheck import unknown_keys

ENV_PATH = Path(".env")  # рабочий каталог бота — /opt/homebot (WorkingDirectory в homebot.service)
QBIT_FIELDS = ("qbit_username", "qbit_password")


def not_writable_text(settings: Settings) -> str:
    if settings.homebot_in_container:
        return "в Docker бот не может править .env — поменяйте его на сервере и перезапустите контейнер"
    return "бот пока не может править .env — один раз запустите deploy/update.sh (он разрешит это службе)"


def env_report(settings: Settings, path: Path = ENV_PATH) -> dict[str, Any]:
    known = {name.upper() for name in Settings.model_fields}
    items = envfile.classify(unknown_keys(path), known)
    return {
        "legacy": [{"key": i.key, "reason": i.legacy} for i in items if i.legacy],
        "other": [{"key": i.key, "guess": i.guess} for i in items if not i.legacy],
        "writable": envfile.writable(path),
        "hint": "" if envfile.writable(path) else not_writable_text(settings),
        "backups": len(envfile.backups(settings.state_dir)),
    }


def remove_legacy(settings: Settings, path: Path = ENV_PATH) -> tuple[list[str], Path | None]:
    """Убирает устаревшие ключи; (что убрано, где копия). OSError — нет прав на запись."""
    known = {name.upper() for name in Settings.model_fields}
    keys = [i.key for i in envfile.classify(unknown_keys(path), known) if i.legacy]
    if not keys:
        return [], None
    return keys, envfile.edit(path, settings.state_dir, remove=keys)


@dataclass(frozen=True, slots=True)
class Saved:
    where: str  # "env", "chat" или "memory" (только до перезапуска)
    backup: Path | None = None
    note: str = ""


async def qbit_login(settings: Settings, apps: Apps, username: str, password: str, path: Path = ENV_PATH) -> Saved:
    """Проверяет вход и сохраняет. AppError — вход не удался (ничего не меняется)."""
    qbit = apps.qbit
    if qbit is None:
        raise ValueError("qBittorrent не подключён")
    username, password = username.strip(), password.strip()
    if not username or not password:
        raise ValueError("нужны и логин, и пароль")
    await qbit.check_login(username, password)  # AppError → наверх, ничего не сохраняем
    qbit.set_credentials(username, password)
    # и в общих настройках: если приложения пересоберут из них (подключение из чата), старый пароль не вернётся
    object.__setattr__(settings, "qbit_username", username)
    object.__setattr__(settings, "qbit_password", SecretStr(password))
    if not any(name in settings.model_fields_set for name in QBIT_FIELDS):  # подключали из чата — там и храним
        stored = connect.load(settings.state_dir)
        stored["qbit"] = {**stored.get("qbit", {}), "qbit_username": username, "qbit_password": password}
        connect.save(settings.state_dir, stored)
        return Saved("chat")
    try:
        backup = envfile.edit(path, settings.state_dir, values={"QBIT_USERNAME": username, "QBIT_PASSWORD": password})
    except OSError:
        return Saved("memory", note=not_writable_text(settings))
    return Saved("env", backup)
