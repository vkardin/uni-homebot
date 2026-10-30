"""⬆️ Обновление из чата: проверка архива (бот) и обновлятор (root-скрипт) на песочнице во временных каталогах."""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path

from homebot.services import selfupdate
from homebot.services.selfupdate import ArchiveError, compare_versions, inspect_archive
from homebot.ui.update_texts import render_archive, render_result, render_update_home

updater = selfupdate.updater_module()

CHANGELOG = 'WHATS_NEW = {"3.9.0": ("Первое новшество", "Второе <b>")}\n'


def _files(
    version: str = "3.9.0", *, deploy: str = "#!/bin/bash\n", requirements: str = "aiogram\n", extra: dict | None = None
) -> dict:
    files = {
        "homebot/homebot/__init__.py": f'__version__ = "{version}"\n',
        "homebot/homebot/main.py": "print('main')\n",
        "homebot/homebot/changelog.py": CHANGELOG,
        "homebot/requirements.txt": requirements,
        "homebot/deploy/update.sh": deploy,
        "homebot/.env.example": "A=1\nB=2\n",
    }
    files.update(extra or {})
    return files


def _tar(path: Path, files: dict, *, links: dict | None = None) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return path


def _install(root: Path, version: str = "3.3.0", *, deploy: str = "#!/bin/bash\n", requirements: str = "aiogram\n") -> Path:
    """Как выглядит /opt/homebot."""
    for name, content in _files(version, deploy=deploy, requirements=requirements, extra={"homebot/homebot/old.py": "old\n"}).items():
        target = root / name.removeprefix("homebot/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    (root / ".env").write_text("A=custom\n")
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").write_text("venv")
    (root / "data").mkdir()
    return root


# ------------------------------------------------------------------------------- проверка архива
def test_versions():
    assert compare_versions("3.4.0", "3.3.0") == 1 and compare_versions("3.3.0", "3.3.0") == 0
    assert compare_versions("3.3.9", "3.4.0") == -1 and compare_versions("4.0.0", "3.12.1") == 1
    assert compare_versions("6.0.0", "5.13.0") == 1 and compare_versions("5.13.0", "6.0.0") == -1


def test_inspect_archive_describes_update():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        installed = _install(tmp_path / "opt")
        info = inspect_archive(_tar(tmp_path / "a.tar.gz", _files()), installed)
        assert info.version == "3.9.0" and info.files == 6 and len(info.sha256) == 64 and info.sig == info.sha256[:12]
        assert info.whats_new == ("Первое новшество", "Второе <b>")
        assert info.system_changed == () and info.deps_changed is False
        text = render_archive(info, "3.3.0")
        assert "v3.3.0 → <b>v3.9.0</b>" in text and "Второе &lt;b&gt;" in text and "те же" in text

        changed = inspect_archive(
            _tar(tmp_path / "b.tar.gz", _files(deploy="#!/bin/bash\necho new\n", requirements="aiogram\nnumpy\n")), installed
        )
        assert changed.system_changed == ("update.sh",) and changed.deps_changed is True
        assert "системные файлы" in render_archive(changed, "3.3.0")

        same = inspect_archive(_tar(tmp_path / "c.tar.gz", _files("3.3.0")), installed)
        assert "та же версия" in render_archive(same, "3.3.0")
        older = inspect_archive(_tar(tmp_path / "d.tar.gz", _files("3.0.0")), installed)
        assert "старее" in render_archive(older, "3.3.0")


def test_inspect_rejects_unsafe_and_foreign_archives():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        installed = _install(tmp_path / "opt")
        bad_cases = {
            "absolute": _files(extra={"/etc/passwd": "x"}),
            "dotdot": _files(extra={"homebot/../evil.py": "x"}),
            "other-top": _files(extra={"other/file.py": "x"}),
            "no-main": {k: v for k, v in _files().items() if not k.endswith("main.py")},
            "no-version": _files(extra={"homebot/homebot/__init__.py": "# без версии\n"}),
        }
        for name, files in bad_cases.items():
            try:
                inspect_archive(_tar(tmp_path / f"{name}.tar.gz", files), installed)
            except ArchiveError:
                continue
            raise AssertionError(f"{name}: архив должен быть отклонён")

        for link_name, target in (("homebot/link", "/etc/passwd"), ("homebot/homebot/x", "../../..")):
            try:
                inspect_archive(_tar(tmp_path / "l.tar.gz", _files(), links={link_name: target}), installed)
            except ArchiveError as exc:
                assert "ссылка" in str(exc)
            else:
                raise AssertionError("символическая ссылка должна быть отклонена")

        junk = tmp_path / "junk.tar.gz"
        junk.write_bytes(b"not an archive")
        for path in (junk, tmp_path / "missing.tar.gz"):
            try:
                inspect_archive(path, installed)
            except ArchiveError:
                continue
            raise AssertionError("не-архив должен быть отклонён")


# ---------------------------------------------------------------------------- обмен файлами с обновлятором
def test_request_result_and_ready_files():
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        info = selfupdate.ArchiveInfo("3.9.0", "ab" * 32, 10, 3)
        assert not selfupdate.request_pending(state)
        selfupdate.write_request(state, "install", info)
        assert selfupdate.request_pending(state)
        data = json.loads((state / "update" / "request.json").read_text())
        assert data["action"] == "install" and data["sha256"] == "ab" * 32 and data["to_version"] == "3.9.0"
        assert selfupdate.read_result(state) is None
        (state / "update" / "result.json").write_text('{"ok": true}')
        assert selfupdate.read_result(state) == {"ok": True}
        selfupdate.clear_result(state)
        assert selfupdate.read_result(state) is None
        selfupdate.write_ready(state, "3.9.0")
        ready = json.loads((state / "ready.json").read_text())
        assert ready["version"] == "3.9.0" and isinstance(ready["pid"], int)


def test_previous_version_and_texts():
    with tempfile.TemporaryDirectory() as tmp:
        prev = _install(Path(tmp) / "prev", "3.2.0")
        assert selfupdate.previous_version(prev) == "3.2.0" and selfupdate.previous_version(Path(tmp) / "none") is None
    assert "Обновлятор не установлен" in render_update_home("3.3.0", ready=False, previous=None)
    home = render_update_home("3.3.0", ready=True, previous="3.2.0")
    assert "v3.3.0" in home and "для отката" in home and ".tar.gz" in home


def test_result_texts():
    ok = render_result(
        {
            "ok": True,
            "stage": "done",
            "from_version": "3.3.0",
            "to_version": "3.4.0",
            "tests": "313 passed",
            "deps": True,
            "env_added": ["X", "Y"],
        }
    )
    assert "✅" in ok and "v3.3.0 → v3.4.0" in ok and "313 passed" in ok and "X, Y" in ok
    failed = render_result({"ok": False, "stage": "tests", "to_version": "3.4.0", "message": "тесты <упали>"})
    assert "❌" in failed and "код не менялся" in failed and "&lt;упали&gt;" in failed
    back = render_result({"ok": False, "stage": "start", "to_version": "3.4.0", "message": "не поднялся", "rolled_back": True})
    assert "Вернул прежнюю версию" in back
    assert "Откат выполнен" in render_result({"ok": True, "stage": "rollback", "from_version": "3.4.0", "to_version": "3.3.0"})
    assert "обычная установка" in render_result({"ok": False, "stage": "system", "message": "x"})


# ------------------------------------------------------------------------------------ обновлятор
class Harness:
    def __init__(self, tmp: Path, *, tests_ok: bool = True, bad_versions: tuple[str, ...] = (), pid_mismatch: bool = False) -> None:
        self.paths = updater.Paths(dest=tmp / "opt", prev=tmp / "opt.prev", state=tmp / "state")
        self.paths.update_dir.mkdir(parents=True)
        self.clock = 1000.0
        self.tests_ok = tests_ok
        self.bad_versions = bad_versions
        self.pid_mismatch = pid_mismatch
        self.calls: list[list[str]] = []

    def sleep(self, seconds: float) -> None:
        self.clock += seconds

    def now(self) -> float:
        return self.clock

    def run(self, cmd, *, cwd=None, timeout=600):
        self.calls.append(list(cmd))
        done = subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["systemctl", "restart"]:
            version = updater.read_version(self.paths.dest / "homebot" / "__init__.py")
            if version not in self.bad_versions:
                self.paths.ready.write_text(json.dumps({"version": version, "pid": 4242}))
        elif cmd[:2] == ["systemctl", "is-active"]:
            done.stdout = "active\n"
        elif "MainPID" in cmd:
            done.stdout = "9999\n" if self.pid_mismatch else "4242\n"
        elif "pytest" in cmd and "-m" in cmd:
            done.returncode = 0 if self.tests_ok else 1
            done.stdout = "313 passed, 1 skipped in 9.1s\n" if self.tests_ok else "1 failed, 312 passed in 9.1s\n"
        return done

    def updater(self):
        return updater.Updater(
            self.paths, run=self.run, sleep=self.sleep, now=self.now, privileged=False, stable_sec=0, ready_timeout=20
        )

    def request(self, archive: Path | None, **extra) -> None:
        data: dict = {"action": "install", "to_version": "3.9.0"}
        if archive is not None:
            archive.replace(self.paths.archive)
            data["sha256"] = updater.sha256_file(self.paths.archive)
        data.update(extra)
        self.paths.request.write_text(json.dumps(data))

    def result(self) -> dict:
        return json.loads(self.paths.result.read_text())

    def restarts(self) -> int:
        return sum(1 for c in self.calls if c[:2] == ["systemctl", "restart"])


def _setup(tmp: Path, **kw) -> tuple[Harness, Path]:
    harness = Harness(tmp, **kw)
    _install(harness.paths.dest)
    return harness, tmp


def test_updater_installs_new_version_and_keeps_user_data():
    with tempfile.TemporaryDirectory() as tmp:
        harness, root = _setup(Path(tmp))
        harness.request(_tar(root / "a.tar.gz", _files("3.9.0")))
        assert harness.updater().run() is True
        result = harness.result()
        assert result["ok"] is True and result["from_version"] == "3.3.0" and result["to_version"] == "3.9.0"
        assert "313 passed" in result["tests"] and result["env_added"] == ["B"]
        dest = harness.paths.dest
        assert updater.read_version(dest / "homebot" / "__init__.py") == "3.9.0"
        assert not (dest / "homebot" / "old.py").exists()  # файла нет в новом архиве — удалён
        env = (dest / ".env").read_text()
        assert env.startswith("A=custom\n") and "B=2" in env and env.count("A=") == 1  # свои значения целы, новое дописано
        assert (dest / ".venv" / "bin" / "python").read_text() == "venv" and (dest / "data").is_dir()
        assert updater.read_version(harness.paths.prev / "homebot" / "__init__.py") == "3.3.0"  # копия для отката
        assert not harness.paths.request.exists() and not harness.paths.archive.exists() and not harness.paths.work.exists()
        assert harness.restarts() == 1


def test_updater_refuses_when_tests_fail_without_touching_code():
    with tempfile.TemporaryDirectory() as tmp:
        harness, root = _setup(Path(tmp), tests_ok=False)
        harness.request(_tar(root / "a.tar.gz", _files("3.9.0")))
        assert harness.updater().run() is False
        result = harness.result()
        assert result["ok"] is False and result["stage"] == "tests" and "1 failed" in result["message"]
        assert updater.read_version(harness.paths.dest / "homebot" / "__init__.py") == "3.3.0"
        assert not harness.paths.prev.exists() and harness.restarts() == 0


def test_updater_refuses_system_file_changes():
    with tempfile.TemporaryDirectory() as tmp:
        harness, root = _setup(Path(tmp))
        harness.request(_tar(root / "a.tar.gz", _files("3.9.0", deploy="#!/bin/bash\necho changed\n")))
        harness.updater().run()
        result = harness.result()
        assert result["stage"] == "system" and "update.sh" in result["message"] and "sudo bash" in result["message"]
        assert updater.read_version(harness.paths.dest / "homebot" / "__init__.py") == "3.3.0" and harness.restarts() == 0


def test_updater_rolls_back_when_new_version_does_not_start():
    with tempfile.TemporaryDirectory() as tmp:
        harness, root = _setup(Path(tmp), bad_versions=("3.9.0",))
        harness.request(_tar(root / "a.tar.gz", _files("3.9.0")))
        assert harness.updater().run() is False
        result = harness.result()
        assert result["ok"] is False and result["stage"] == "start" and result["rolled_back"] is True
        assert updater.read_version(harness.paths.dest / "homebot" / "__init__.py") == "3.3.0"
        assert not (harness.paths.dest / "homebot" / "main.py").read_text().startswith("new")
        assert harness.restarts() == 2  # сначала новая, потом откат


def test_updater_rolls_back_when_bot_crashes_right_after_start():
    with tempfile.TemporaryDirectory() as tmp:
        harness, root = _setup(Path(tmp), pid_mismatch=True)
        harness.request(_tar(root / "a.tar.gz", _files("3.9.0")))
        harness.updater().run()
        result = harness.result()
        assert result["stage"] == "start" and "перезапустился" in result["message"]


def test_updater_rejects_wrong_checksum_and_missing_archive():
    with tempfile.TemporaryDirectory() as tmp:
        harness, root = _setup(Path(tmp))
        harness.request(_tar(root / "a.tar.gz", _files("3.9.0")), sha256="0" * 64)
        harness.updater().run()
        assert harness.result()["stage"] == "archive" and "контрольная сумма" in harness.result()["message"]
        harness.request(None, sha256="0" * 64)
        harness.updater().run()
        assert harness.result()["stage"] == "archive"
        wrong = _tar(root / "b.tar.gz", _files("3.9.0"))
        harness.request(wrong, to_version="4.0.0")
        harness.updater().run()
        assert "версия в архиве 3.9.0" in harness.result()["message"]


def test_updater_rejects_unsafe_archive():
    with tempfile.TemporaryDirectory() as tmp:
        harness, root = _setup(Path(tmp))
        harness.request(_tar(root / "a.tar.gz", _files("3.9.0"), links={"homebot/evil": "/etc/shadow"}))
        harness.updater().run()
        assert harness.result()["stage"] == "archive" and "ссылка" in harness.result()["message"]
        assert harness.restarts() == 0


def test_updater_rollback_action():
    with tempfile.TemporaryDirectory() as tmp:
        harness, root = _setup(Path(tmp))
        harness.request(None, action="rollback")
        harness.updater().run()
        assert harness.result()["stage"] == "rollback" and harness.result()["ok"] is False  # откатываться не на что
        harness.request(_tar(root / "a.tar.gz", _files("3.9.0")))
        harness.updater().run()
        assert harness.result()["ok"] is True
        harness.paths.request.write_text(json.dumps({"action": "rollback"}))
        assert harness.updater().run() is True
        result = harness.result()
        assert result["stage"] == "rollback" and result["to_version"] == "3.3.0"
        assert updater.read_version(harness.paths.dest / "homebot" / "__init__.py") == "3.3.0"


def test_updater_ignores_missing_or_broken_request():
    with tempfile.TemporaryDirectory() as tmp:
        harness, _ = _setup(Path(tmp))
        assert harness.updater().run() is False and not harness.paths.result.exists()
        harness.paths.request.write_text("{не json")
        assert harness.updater().run() is False and not harness.paths.result.exists()


def test_sync_tree_keeps_user_dirs_and_removes_stale_files():
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "src", Path(tmp) / "dst"
        (src / "pkg").mkdir(parents=True)
        (src / "pkg" / "a.py").write_text("new")
        (src / ".env").write_text("SRC-ENV")  # из источника .env не копируется
        (dst / "pkg" / "old").mkdir(parents=True)
        (dst / "pkg" / "old" / "x.py").write_text("x")
        (dst / "pkg" / "b.py").write_text("stale")
        (dst / ".env").write_text("MINE")
        (dst / "data").mkdir()
        (dst / "data" / "db").write_text("keep")
        updater.sync_tree(src, dst)
        assert (dst / "pkg" / "a.py").read_text() == "new"
        assert not (dst / "pkg" / "b.py").exists() and not (dst / "pkg" / "old").exists()
        assert (dst / ".env").read_text() == "MINE" and (dst / "data" / "db").read_text() == "keep"


def test_merge_env_adds_only_missing_keys():
    with tempfile.TemporaryDirectory() as tmp:
        env, example = Path(tmp) / ".env", Path(tmp) / ".env.example"
        env.write_text("A=mine\n# C=commented\n")
        example.write_text("# комментарий\nA=default\nB=2\nC=3\nD=\n")
        assert updater.merge_env(env, example) == ["B", "D"]
        text = env.read_text()
        assert "A=mine" in text and "A=default" not in text and "B=2" in text and text.count("C=") == 1
        assert updater.merge_env(env, example) == []


def test_report_waits_for_result_while_updater_busy():
    import asyncio
    import fcntl
    from types import SimpleNamespace

    from homebot import app

    class FakeBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))

    async def scenario(state: Path):
        upd = state / "update"
        upd.mkdir()
        settings = SimpleNamespace(state_dir=state, admin_id=7)
        bot = FakeBot()
        # обновлятор ещё держит lock: итог появится позже — бот должен дождаться
        with (upd / "lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            assert selfupdate.updater_busy(state)
            task = asyncio.create_task(app._report_update(bot, settings, wait=5, step=0.05))
            await asyncio.sleep(0.2)
            assert bot.sent == []
            (upd / "result.json").write_text('{"ok": true, "stage": "done", "from_version": "1.0.0", "to_version": "1.1.0"}')
            await asyncio.wait_for(task, 2)
        assert not selfupdate.updater_busy(state)
        assert len(bot.sent) == 1 and bot.sent[0][0] == 7 and "1.1.0" in bot.sent[0][1]
        assert selfupdate.read_result(state) is None
        # обновлятор не работает и итога нет — сразу выходим, ничего не шлём
        await asyncio.wait_for(app._report_update(bot, settings, wait=5, step=0.05), 1)
        assert len(bot.sent) == 1

    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(scenario(Path(tmp)))
