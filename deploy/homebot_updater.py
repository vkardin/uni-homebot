#!/usr/bin/python3
"""Обновлятор HomeBot: ставит архив, присланный боту в чат. Работает от root, только стандартная библиотека.

Ставится в /usr/local/sbin/homebot-updater скриптом deploy/update.sh (в корневое место, куда бот писать не может)
и запускается systemd (homebot-update.service) при появлении файла запроса /var/lib/homebot/update/request.json.

Принципы безопасности:
* от root НЕ выполняется ничего из архива: он распаковывается, проверяется, а тесты идут от имени homebot;
* меняется только код бота (+ новые параметры .env). Если в архиве изменились системные файлы (deploy/*: unit-файлы,
  polkit, этот обновлятор), обновление отклоняется — нужна обычная установка `sudo bash deploy/update.sh`;
* перед заменой кода копия прежнего лежит в /opt/homebot.prev; если новая версия не поднялась — возвращается она.

Запрос (request.json): {"action": "install"|"rollback", "sha256": "...", "to_version": "3.4.0"}.
Итог (result.json) бот читает при следующем запуске и присылает в чат.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SERVICE = "homebot"
USER = "homebot"
TOP = "homebot"
MAX_ARCHIVE = 20 * 1024 * 1024
MAX_MEMBERS = 5000
MAX_UNPACKED = 80 * 1024 * 1024
STABLE_SEC = 8  # после «готов» бот должен ещё столько секунд прожить без перезапусков
READY_TIMEOUT = 75
KEEP = (".env", ".venv", "data")  # верхний уровень, который синхронизация не трогает
REQUIRED = (
    f"{TOP}/homebot/__init__.py",
    f"{TOP}/homebot/main.py",
    f"{TOP}/requirements.txt",
    f"{TOP}/deploy/update.sh",
)


class UpdateError(Exception):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message


@dataclass(frozen=True)
class Paths:
    dest: Path = Path("/opt/homebot")
    prev: Path = Path("/opt/homebot.prev")
    state: Path = Path("/var/lib/homebot")

    @property
    def update_dir(self) -> Path:
        return self.state / "update"

    @property
    def request(self) -> Path:
        return self.update_dir / "request.json"

    @property
    def archive(self) -> Path:
        return self.update_dir / "incoming.tar.gz"

    @property
    def work(self) -> Path:
        return self.update_dir / "work"

    @property
    def result(self) -> Path:
        return self.update_dir / "result.json"

    @property
    def lock(self) -> Path:
        return self.update_dir / "lock"

    @property
    def ready(self) -> Path:
        return self.state / "ready.json"


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def default_runner(cmd: list[str], *, cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)


# ----------------------------------------------------------------------------- архив
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Только обычные файлы и каталоги внутри `homebot/`, без абсолютных путей, «..», ссылок и устройств."""
    members = tar.getmembers()
    if not members:
        raise UpdateError("archive", "архив пустой")
    if len(members) > MAX_MEMBERS:
        raise UpdateError("archive", "в архиве слишком много файлов")
    total = 0
    for member in members:
        name = member.name
        path = PurePosixPath(name)
        if "\\" in name or "\x00" in name or path.is_absolute() or ".." in path.parts or not path.parts:
            raise UpdateError("archive", f"небезопасный путь в архиве: {name[:80]!r}")
        if path.parts[0] != TOP:
            raise UpdateError("archive", "внутри архива должна быть одна папка homebot/")
        if not (member.isreg() or member.isdir()):
            raise UpdateError("archive", f"в архиве ссылка или особый файл: {name[:80]!r}")
        total += max(member.size, 0)
        if member.size > MAX_ARCHIVE or total > MAX_UNPACKED:
            raise UpdateError("archive", "архив слишком большой после распаковки")
    names = {PurePosixPath(m.name).as_posix().rstrip("/") for m in members}
    for required in REQUIRED:
        if required not in names:
            raise UpdateError("archive", f"в архиве нет {required}")
    return members


def read_version(path: Path) -> str:
    match = re.search(r'^__version__ = "([0-9][0-9A-Za-z.+-]*)"', path.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise UpdateError("archive", "не нашёл версию в homebot/__init__.py")
    return match.group(1)


def tree_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not root.is_dir():
        return result
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            result[path.relative_to(root).as_posix()] = sha256_file(path)
    return result


def sync_tree(src: Path, dst: Path) -> None:
    """Делает dst копией src, не трогая верхнеуровневые KEEP (.env, .venv, data) и кэши Python."""
    dst.mkdir(parents=True, exist_ok=True)
    wanted: set[Path] = set()
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if rel.parts[0] in KEEP or "__pycache__" in rel.parts or rel.parts[0] in {".pytest_cache", ".ruff_cache"}:
            continue
        wanted.add(rel)
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or sha256_file(target) != sha256_file(path):
                shutil.copy2(path, target)
    for path in sorted(dst.rglob("*"), reverse=True):
        rel = path.relative_to(dst)
        if rel.parts[0] in KEEP or "__pycache__" in rel.parts or rel in wanted:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def merge_env(env_file: Path, example: Path) -> list[str]:
    """Дописывает в .env новые параметры из .env.example (ваши значения не меняет). Возвращает имена добавленных."""
    if not env_file.is_file() or not example.is_file():
        return []
    current = env_file.read_text(encoding="utf-8")
    added: list[str] = []
    lines: list[str] = []
    for line in example.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z_][A-Z0-9_]*)=", line)
        if match is None:
            continue
        key = match.group(1)
        if re.search(rf"^#?\s*{key}=", current, re.MULTILINE):
            continue
        lines.append(line)
        added.append(key)
    if added:
        header = f"\n# --- добавлено обновлятором {time.strftime('%F')} ---\n"
        env_file.write_text(current.rstrip("\n") + "\n" + header + "\n".join(lines) + "\n", encoding="utf-8")
    return added


# ------------------------------------------------------------------------- обновлятор
class Updater:
    def __init__(
        self,
        paths: Paths | None = None,
        *,
        run: Runner = default_runner,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        privileged: bool | None = None,
        stable_sec: float = STABLE_SEC,
        ready_timeout: float = READY_TIMEOUT,
    ) -> None:
        self.p = paths or Paths()
        self._run = run
        self._sleep = sleep
        self._now = now
        self.privileged = (os.geteuid() == 0) if privileged is None else privileged
        self._stable = stable_sec
        self._ready_timeout = ready_timeout

    # --- вспомогательное ---------------------------------------------------------------
    def _as_user(self, cmd: list[str]) -> list[str]:
        return ["runuser", "-u", USER, "--", *cmd] if self.privileged else cmd

    def _chown(self, path: Path) -> None:
        """chown -R homebot:homebot (без .venv: он и так принадлежит homebot, а файлов в нём тысячи)."""
        if not self.privileged:
            return
        if path.is_file():
            shutil.chown(path, USER, USER)
            return
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d != ".venv"]
            for name in [".", *dirs, *files]:
                try:
                    shutil.chown(Path(root) / name, USER, USER)
                except OSError:
                    pass

    def _result(self, **data: object) -> None:
        data["at"] = int(self._now())
        tmp = self.p.result.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.p.result)
        self._chown(self.p.result)

    def _installed_version(self) -> str:
        try:
            return read_version(self.p.dest / "homebot" / "__init__.py")
        except (OSError, UpdateError):
            return "?"

    # --- состояние бота ----------------------------------------------------------------
    def _restart_and_wait(self, version: str | None) -> str | None:
        """Перезапускает службу и ждёт, пока бот напишет «готов». Возвращает причину отказа или None."""
        self.p.ready.unlink(missing_ok=True)
        self._run(["systemctl", "restart", SERVICE])
        deadline = self._now() + self._ready_timeout
        info: dict | None = None
        while self._now() < deadline:
            self._sleep(2)
            try:
                loaded = json.loads(self.p.ready.read_text(encoding="utf-8"))
                info = loaded if isinstance(loaded, dict) else None
            except (OSError, ValueError):
                info = None
            if info is not None and (version is None or info.get("version") == version):
                break
            info = None
        if info is None:
            tail = self._run(["journalctl", "-u", SERVICE, "-n", "6", "--no-pager", "-o", "cat"]).stdout.strip()
            return f"бот не сообщил о готовности за {self._ready_timeout:.0f} с. {tail[-300:]}"
        self._sleep(self._stable)
        active = self._run(["systemctl", "is-active", SERVICE]).stdout.strip()
        pid = self._run(["systemctl", "show", "-p", "MainPID", "--value", SERVICE]).stdout.strip()
        if active != "active" or str(info.get("pid")) != pid:
            return "бот запустился, но тут же перезапустился (падает после старта)"
        return None

    # --- шаги --------------------------------------------------------------------------
    def _prepare(self, request: dict) -> Path:
        """Проверяет и распаковывает архив. Возвращает каталог с кодом (…/work/homebot)."""
        archive = self.p.archive
        if not archive.is_file():
            raise UpdateError("archive", "архив не найден (его уже удалили?)")
        if archive.stat().st_size > MAX_ARCHIVE:
            raise UpdateError("archive", "архив больше 20 МБ")
        if sha256_file(archive) != str(request.get("sha256", "")):
            raise UpdateError("archive", "контрольная сумма архива не совпала — отправьте файл заново")
        shutil.rmtree(self.p.work, ignore_errors=True)
        self.p.work.mkdir(parents=True)
        try:
            with tarfile.open(archive, "r:gz") as tar:
                members = validate_members(tar)
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(self.p.work, members=members, filter="data")
                else:  # pragma: no cover - Python < 3.11.4
                    tar.extractall(self.p.work, members=members)
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise UpdateError("archive", f"не удалось распаковать архив: {exc}") from exc
        src = self.p.work / TOP
        version = read_version(src / "homebot" / "__init__.py")
        expected = str(request.get("to_version", ""))
        if expected and version != expected:
            raise UpdateError("archive", f"версия в архиве {version}, а запрошена {expected}")
        return src

    def _check_system_files(self, src: Path) -> None:
        old, new = tree_hashes(self.p.dest / "deploy"), tree_hashes(src / "deploy")
        changed = sorted({name for name in old.keys() | new.keys() if old.get(name) != new.get(name)})
        if changed:
            shown = ", ".join(changed[:4]) + ("…" if len(changed) > 4 else "")
            raise UpdateError("system", f"изменились системные файлы ({shown}). Нужна обычная установка: sudo bash deploy/update.sh")

    def _dependencies(self, src: Path) -> bool:
        new, old = src / "requirements.txt", self.p.dest / "requirements.txt"
        if old.is_file() and old.read_bytes() == new.read_bytes():
            return False
        python = self.p.dest / ".venv" / "bin" / "pip"
        out = self._run(self._as_user([str(python), "install", "--quiet", "--disable-pip-version-check", "-r", str(new)]), timeout=900)
        if out.returncode != 0:
            raise UpdateError("deps", "не удалось поставить зависимости: " + (out.stderr or out.stdout).strip()[-300:])
        return True

    def _tests(self, src: Path) -> str:
        venv_python = str(self.p.dest / ".venv" / "bin" / "python")
        probe = self._run(self._as_user([venv_python, "-c", "import pytest"]))
        if probe.returncode != 0:
            self._run(
                self._as_user(
                    [str(self.p.dest / ".venv" / "bin" / "pip"), "install", "--quiet", "--disable-pip-version-check", "pytest"]
                ),
                timeout=600,
            )
        cmd = ["env", "PYTHONDONTWRITEBYTECODE=1", venv_python, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        out = self._run(self._as_user(cmd), cwd=src, timeout=900)
        tail = (out.stdout or "").strip().splitlines()
        summary = tail[-1].strip() if tail else ""
        if out.returncode != 0:
            raise UpdateError("tests", "тесты новой версии не прошли: " + summary[:200])
        return summary

    def _install(self, request: dict) -> None:
        before = self._installed_version()
        src = self._prepare(request)
        self._check_system_files(src)
        self._chown(self.p.work)
        deps = self._dependencies(src)
        summary = self._tests(src)
        target = read_version(src / "homebot" / "__init__.py")

        shutil.rmtree(self.p.prev, ignore_errors=True)
        sync_tree(self.p.dest, self.p.prev)
        sync_tree(src, self.p.dest)
        self._chown(self.p.dest)
        added = merge_env(self.p.dest / ".env", self.p.dest / ".env.example")
        reason = self._restart_and_wait(target)
        if reason is None:
            self._result(ok=True, stage="done", from_version=before, to_version=target, tests=summary, deps=deps, env_added=added)
            return
        # Не поднялась — возвращаем прежний код.
        sync_tree(self.p.prev, self.p.dest)
        self._chown(self.p.dest)
        back = self._restart_and_wait(None)
        self._result(
            ok=False,
            stage="start",
            rolled_back=True,
            from_version=before,
            to_version=target,
            message=reason + ("" if back is None else f" Откат тоже не удался: {back}"),
        )

    def _rollback(self) -> None:
        if not (self.p.prev / "homebot" / "__init__.py").is_file():
            raise UpdateError("rollback", "прежней версии нет (обновлений из чата ещё не было)")
        before = self._installed_version()
        target = read_version(self.p.prev / "homebot" / "__init__.py")
        sync_tree(self.p.prev, self.p.dest)
        self._chown(self.p.dest)
        reason = self._restart_and_wait(target)
        if reason is not None:
            raise UpdateError("rollback", "после отката бот не поднялся: " + reason)
        self._result(ok=True, stage="rollback", from_version=before, to_version=target, rolled_back=True)

    # --- точка входа ---------------------------------------------------------------------
    def run(self) -> bool:
        self.p.update_dir.mkdir(parents=True, exist_ok=True)
        with self.p.lock.open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False  # уже идёт другое обновление
            try:
                request = json.loads(self.p.request.read_text(encoding="utf-8"))
                if not isinstance(request, dict):
                    raise ValueError
            except (OSError, ValueError):
                return False
            self.p.request.unlink(missing_ok=True)  # чтобы path-юнит не запускал нас по кругу
            try:
                if request.get("action") == "rollback":
                    self._rollback()
                else:
                    self._install(request)
            except UpdateError as exc:
                self._result(
                    ok=False,
                    stage=exc.stage,
                    message=exc.message,
                    from_version=self._installed_version(),
                    to_version=str(request.get("to_version", "")),
                )
            except Exception as exc:
                self._result(
                    ok=False, stage="crash", message=f"{type(exc).__name__}: {exc}"[:300], from_version=self._installed_version()
                )
            finally:
                shutil.rmtree(self.p.work, ignore_errors=True)
                self.p.archive.unlink(missing_ok=True)
        try:
            return bool(json.loads(self.p.result.read_text(encoding="utf-8")).get("ok"))
        except (OSError, ValueError):
            return False


def main() -> int:
    return 0 if Updater().run() else 1


if __name__ == "__main__":
    sys.exit(main())
