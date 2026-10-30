"""⬆️ Обновление из чата: проверка присланного архива и передача его root-обновлятору.

Бот сам ничего не устанавливает (у него нет прав): он проверяет архив, кладёт его в `<STATE_DIR>/update/` и пишет
`request.json`. Файл замечает systemd (homebot-update.path) и запускает /usr/local/sbin/homebot-updater — это
deploy/homebot_updater.py, поставленный скриптом update.sh. Результат обновлятор кладёт в `result.json`, а бот присылает
его в чат при следующем запуске.

Проверка архива (безопасные пути, никаких ссылок, лимиты) — та же функция, что использует обновлятор.
"""

from __future__ import annotations

import ast
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]  # каталог, из которого работает бот (/opt/homebot)
UPDATER_PATH = Path("/usr/local/sbin/homebot-updater")
PREV_DIR = Path("/opt/homebot.prev")
CONFIRM_TTL_SEC = 900
_updater: ModuleType | None = None


class ArchiveError(ValueError):
    """Присланный файл не подходит. Текст безопасно показывать пользователю."""


def updater_module() -> ModuleType:
    """deploy/homebot_updater.py как модуль (общая проверка архива без копирования кода)."""
    global _updater
    if _updater is None:
        path = ROOT / "deploy" / "homebot_updater.py"
        spec = importlib.util.spec_from_file_location("homebot_updater", path)
        if spec is None or spec.loader is None:
            raise ArchiveError("не нашёл deploy/homebot_updater.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["homebot_updater"] = module
        spec.loader.exec_module(module)
        _updater = module
    return _updater


@dataclass(frozen=True, slots=True)
class ArchiveInfo:
    version: str
    sha256: str
    size: int
    files: int
    whats_new: tuple[str, ...] = ()
    system_changed: tuple[str, ...] = ()  # изменившиеся файлы deploy/ — их из чата поставить нельзя
    deps_changed: bool = False

    @property
    def sig(self) -> str:
        return self.sha256[:12]


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version.split("+")[0])[:4])


def compare_versions(new: str, current: str) -> int:
    a, b = version_key(new), version_key(current)
    return (a > b) - (a < b)


def _parse_whats_new(source: str, version: str) -> tuple[str, ...]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        if target == "WHATS_NEW" and value is not None:
            try:
                data = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return ()
            lines = data.get(version) if isinstance(data, dict) else None
            return tuple(str(line) for line in lines) if isinstance(lines, tuple | list) else ()
    return ()


def inspect_archive(path: Path, installed: Path = ROOT) -> ArchiveInfo:
    """Проверяет архив и описывает его. Ничего не распаковывает на диск."""
    module = updater_module()
    if not path.is_file():
        raise ArchiveError("файл не найден")
    size = path.stat().st_size
    if size > module.MAX_ARCHIVE:
        raise ArchiveError("архив больше 20 МБ")
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = module.validate_members(tar)
            top = module.TOP

            def text(name: str) -> bytes:
                handle = tar.extractfile(f"{top}/{name}")
                return handle.read() if handle is not None else b""

            version_match = re.search(rb'^__version__ = "([0-9][0-9A-Za-z.+-]*)"', text("homebot/__init__.py"), re.MULTILINE)
            if version_match is None:
                raise ArchiveError("не нашёл версию в homebot/__init__.py")
            version = version_match.group(1).decode()
            whats_new = _parse_whats_new(text("homebot/changelog.py").decode("utf-8", "replace"), version)
            new_deploy: dict[str, str] = {}
            for member in members:
                name = member.name
                if member.isreg() and name.startswith(f"{top}/deploy/") and "__pycache__" not in name:
                    handle = tar.extractfile(member)
                    new_deploy[name.removeprefix(f"{top}/deploy/")] = hashlib.sha256(handle.read() if handle else b"").hexdigest()
            new_requirements = text("requirements.txt")
    except module.UpdateError as exc:
        raise ArchiveError(exc.message) from exc
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ArchiveError("это не архив .tar.gz или он повреждён") from exc

    old_deploy = module.tree_hashes(installed / "deploy")
    changed = tuple(sorted(name for name in old_deploy.keys() | new_deploy.keys() if old_deploy.get(name) != new_deploy.get(name)))
    old_requirements = installed / "requirements.txt"
    deps = not old_requirements.is_file() or old_requirements.read_bytes() != new_requirements
    return ArchiveInfo(version, module.sha256_file(path), size, len(members), whats_new, changed, deps)


# ------------------------------------------------------------------------------ обмен файлами
def update_dir(state_dir: Path) -> Path:
    return state_dir / "update"


def archive_path(state_dir: Path) -> Path:
    return update_dir(state_dir) / "incoming.tar.gz"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # атомарно: path-юнит не увидит недописанный файл


def write_request(state_dir: Path, action: str, info: ArchiveInfo | None = None) -> None:
    data: dict[str, Any] = {"action": action, "at": int(time.time())}
    if info is not None:
        data.update(sha256=info.sha256, to_version=info.version)
    _write_json(update_dir(state_dir) / "request.json", data)


def request_pending(state_dir: Path) -> bool:
    return (update_dir(state_dir) / "request.json").exists()


def read_result(state_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((update_dir(state_dir) / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def updater_busy(state_dir: Path) -> bool:
    """Обновлятор сейчас работает (держит свой lock) — значит, итог ещё впереди."""
    try:
        with (update_dir(state_dir) / "lock").open("rb") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError:
        pass
    return False


def clear_result(state_dir: Path) -> None:
    (update_dir(state_dir) / "result.json").unlink(missing_ok=True)


def write_ready(state_dir: Path, version: str) -> None:
    """Отметка «бот поднялся»: по ней обновлятор понимает, что новая версия завелась."""
    _write_json(state_dir / "ready.json", {"version": version, "pid": os.getpid(), "at": int(time.time())})


def updater_installed() -> bool:
    return UPDATER_PATH.is_file()


def previous_version(prev_dir: Path = PREV_DIR) -> str | None:
    try:
        text = (prev_dir / "homebot" / "__init__.py").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None
