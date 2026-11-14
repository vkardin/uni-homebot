"""10.3: авторемонт — .env с резервной копией, контейнеры с зависимостями и паузами, логин qBittorrent."""

import asyncio
import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

from homebot.config import Settings
from homebot.jobs.apps_watch import DockerWatchJob
from homebot.services import envfile, fixes
from homebot.services.apps_http import AppError
from homebot.services.autofix import BACKOFF, MAX_ATTEMPTS, ContainerFixer, dependencies, hint, plan
from homebot.services.docker_api import parse_containers
from homebot.services.state import StateStore

ENV = """# комментарий
BOT_TOKEN=1:abc
DIGEST_ENABLED=true
DIGEST_TIME=08:00
QBIT_USERNAME=admin
QBIT_PASSWORD=old
AIR_ALERT_OBLAST=Київ
ALERTS_IN_UA_TOKEN=secret
RSS_ITEMS_PER_FEED=5
TIMEZON=Europe/Kyiv
"""


def _settings(tmp: Path, **kw) -> Settings:
    return Settings(bot_token="1:abc", admin_id=1, state_dir=tmp / "state", **kw)  # type: ignore[call-arg]


# ------------------------------------------------------------------ .env
def test_legacy_keys_are_classified_and_typos_get_a_guess():
    known = {name.upper() for name in Settings.model_fields}
    items = {i.key: i for i in envfile.classify(["DIGEST_TIME", "AIR_ALERT_INTERVAL_SEC", "ALERTS_IN_UA_TOKEN", "TIMEZON"], known)}
    assert "3.2" in items["DIGEST_TIME"].legacy and items["AIR_ALERT_INTERVAL_SEC"].legacy and items["ALERTS_IN_UA_TOKEN"].legacy
    assert items["TIMEZON"].legacy == "" and items["TIMEZON"].guess == "TIMEZONE"
    assert envfile.legacy_reason("AIRPORT") == ""  # префикс «AIR_» только с подчёркиванием


def test_remove_legacy_keeps_other_lines_and_makes_private_backup():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / ".env"
        path.write_text(ENV, encoding="utf-8")
        removed, backup = fixes.remove_legacy(_settings(root), path)
        assert set(removed) == {"DIGEST_ENABLED", "DIGEST_TIME", "AIR_ALERT_OBLAST", "ALERTS_IN_UA_TOKEN", "RSS_ITEMS_PER_FEED"}
        text = path.read_text(encoding="utf-8")
        assert text == "# комментарий\nBOT_TOKEN=1:abc\nQBIT_USERNAME=admin\nQBIT_PASSWORD=old\nTIMEZON=Europe/Kyiv\n"
        assert backup is not None and backup.read_text(encoding="utf-8") == ENV
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
        assert fixes.remove_legacy(_settings(root), path) == ([], None)  # второй раз — нечего


def test_edit_sets_values_in_place_quotes_and_drops_duplicates():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / ".env"
        path.write_text("A=1\nQBIT_PASSWORD=old\nB=2\nQBIT_PASSWORD=dup\n", encoding="utf-8")
        envfile.edit(path, root, values={"QBIT_PASSWORD": "p@ss word#1", "QBIT_USERNAME": "it's"})
        assert path.read_text(encoding="utf-8") == "A=1\nQBIT_PASSWORD='p@ss word#1'\nB=2\nQBIT_USERNAME=\"it's\"\n"
        try:
            envfile.edit(path, root, values={"X": "a\nB=evil"})
        except ValueError:
            pass
        else:
            raise AssertionError("перевод строки в значении пропустили")


def test_edit_refuses_read_only_and_keeps_only_last_backups():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / ".env"
        path.write_text("A=1\n", encoding="utf-8")
        for i in range(envfile.KEEP + 3):
            envfile.backup(path, root, now=1_700_000_000 + i)
        assert len(envfile.backups(root)) == envfile.KEEP
        if os.geteuid() != 0:  # root пишет и в файл без прав
            path.chmod(0o400)
            assert not envfile.writable(path)
            try:
                envfile.edit(path, root, remove=["A"])
            except OSError:
                pass
            else:
                raise AssertionError("правка файла без прав")


def test_env_report_explains_why_it_cannot_write():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        report = fixes.env_report(_settings(root), root / "нет.env")
        assert report["legacy"] == [] and not report["writable"] and "update.sh" in report["hint"]
        docker = fixes.env_report(_settings(root, homebot_in_container=True), root / "нет.env")
        assert "Docker" in docker["hint"]


# ------------------------------------------------------------------ контейнеры
def _c(name, state="running", status="Up 1 hour", project="romm", service=None, depends=""):
    labels = {"com.docker.compose.project": project, "com.docker.compose.service": service or name} if project else {}
    if depends:
        labels["com.docker.compose.depends_on"] = depends
    return {"Names": ["/" + name], "Image": name, "State": state, "Status": status, "Labels": labels}


PS = [
    _c("romm", "exited", "Exited (137) 1 hour ago", depends="romm-db:service_healthy:false"),
    _c("romm-db", "exited", "Exited (0) 1 hour ago"),
    _c("flaresolverr", "exited", "Exited (255) 2 hours ago", project="arr"),
    _c("homarr", "running", "Up 3 hours (unhealthy)", project="homarr"),
    _c("homarr-db", "running", "Up 3 hours", project="homarr"),
]


def test_dependencies_from_compose_labels_and_by_name_fallback():
    deps = dependencies(parse_containers(PS))
    assert deps["romm"] == ["romm-db"]
    assert deps["homarr"] == ["homarr-db"]  # без метки depends_on — по имени в том же проекте
    assert "flaresolverr" not in deps


def test_plan_starts_stopped_dependency_first_and_skips_running_ones():
    containers = parse_containers(PS)
    assert plan(["romm"], containers) == ["romm-db", "romm"]
    assert plan(["romm", "romm-db"], containers) == ["romm-db", "romm"]
    assert plan(["homarr"], containers) == ["homarr"]  # homarr-db работает — не трогаем
    cyclic = parse_containers([_c("a", depends="b:x:false"), _c("b", "exited", "Exited (1) now", depends="a:x:false")])
    assert plan(["a"], cyclic) == ["b", "a"]


def test_hints_by_exit_code_and_oom():
    c = {x.name: x for x in parse_containers(PS)}
    assert "памят" in hint(c["romm"]) and "swap" in hint(c["romm"])
    assert "лимит контейнера 512 МБ" in hint(c["romm"], {"State": {"OOMKilled": True}, "HostConfig": {"Memory": 512 * 1024**2}})
    assert "журнал" in hint(c["flaresolverr"]) and "вручную" in hint(c["romm-db"])
    assert "проверка здоровья" in hint(c["homarr"])


class Docker:
    """Перезапуск «поднимает» контейнер, если он не в списке `stays_down`."""

    def __init__(self, data, *, stays_down=(), restart_enabled=True):
        self.data = [dict(x) for x in data]
        self.stays_down = set(stays_down)
        self.restart_enabled = restart_enabled
        self.restarted: list[str] = []

    async def containers(self):
        return parse_containers(self.data)

    async def logs(self, name, tail=60):
        return ""

    async def inspect(self, name):
        return {"State": {"OOMKilled": name == "romm"}, "HostConfig": {"Memory": 0}}

    async def restart(self, name):
        self.restarted.append(name)
        if name not in self.stays_down:
            for item in self.data:
                if item["Names"] == ["/" + name]:
                    item["State"], item["Status"] = "running", "Up 1 second"
        return True, "перезапущен"

    def crash(self, name, code=137):
        for item in self.data:
            if item["Names"] == ["/" + name]:
                item["State"], item["Status"] = "exited", f"Exited ({code}) now"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def _no_sleep(_):
    return None


def _fixer(docker, clock=None, **kw):
    return ContainerFixer(docker, clock=clock or Clock(), sleep=_no_sleep, wait_up=0, **kw)  # type: ignore[arg-type]


def test_manual_fix_restarts_dependency_first_and_reports_hint_when_it_stays_down():
    async def scenario():
        docker = Docker(PS, stays_down={"flaresolverr"})
        results = await _fixer(docker).fix(["romm", "flaresolverr"], count=False)
        assert docker.restarted == ["romm-db", "romm", "flaresolverr"]
        by = {r.name: r for r in results}
        assert by["romm-db"].ok and by["romm-db"].dependency and by["romm"].ok
        assert not by["flaresolverr"].ok and "код 255" in by["flaresolverr"].detail

    asyncio.run(scenario())


def test_dependent_is_not_started_when_dependency_fails():
    async def scenario():
        docker = Docker(PS, stays_down={"romm-db"})
        results = await _fixer(docker).fix(["romm"])
        assert docker.restarted == ["romm-db"]
        assert "зависимость romm-db" in results[-1].detail and not results[-1].ok

    asyncio.run(scenario())


def test_policy_backoff_limit_and_single_give_up():
    async def scenario():
        clock = Clock()
        docker = Docker(PS, stays_down={"romm"})
        fixer = _fixer(docker, clock)
        c = {x.name: x for x in await docker.containers()}
        assert fixer.eligible(c["romm"]) and fixer.eligible(c["homarr"]) and fixer.eligible(c["flaresolverr"])
        assert not fixer.eligible(c["romm-db"])  # код 0 — штатная остановка, сам не трогаю
        assert fixer.due("romm")
        await fixer.fix(["romm"])
        assert not fixer.due("romm")
        clock.t += BACKOFF[1]
        assert fixer.due("romm")
        await fixer.fix(["romm"])
        clock.t += BACKOFF[2]
        await fixer.fix(["romm"])
        assert fixer.exhausted("romm") and not fixer.due("romm")
        assert fixer.give_up_once("romm") and not fixer.give_up_once("romm")
        clock.t += 7 * 3600  # окно прошло — снова можно
        assert fixer.due("romm") and fixer.attempts("romm") == 0
        off = _fixer(docker, auto=False)
        assert not off.eligible(c["romm"])
        healed = _fixer(docker, autoheal={"romm-db"})
        assert healed.eligible(c["romm-db"])  # из DOCKER_AUTOHEAL — и после штатной остановки

    asyncio.run(scenario())


class Bot:
    def __init__(self):
        self.sent: list[str] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append(text)
        return SimpleNamespace(message_id=len(self.sent))

    async def edit_message_text(self, **kw):
        return None


def test_watch_job_repairs_container_that_broke_while_watching():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            healthy = [_c("romm"), _c("romm-db"), _c("flaresolverr", project="arr")]
            docker, bot, clock = Docker(healthy, stays_down={"flaresolverr"}), Bot(), Clock()
            fixer = _fixer(docker, clock)
            job = DockerWatchJob(bot=bot, admin_id=1, docker=docker, store=store, ignore=set(), fixer=fixer)  # type: ignore[arg-type]
            await job.tick()
            assert bot.sent == []
            docker.crash("romm")
            docker.crash("romm-db", 0)
            await job.tick()
            assert docker.restarted == ["romm-db", "romm"]
            assert "перезапустил" in bot.sent[-1] and "(зависимость)" in bot.sent[-1]
            await job.tick()
            assert len(bot.sent) == 1  # поднялся сразу — «снова в порядке» не шлём

            docker.crash("flaresolverr", 255)
            await job.tick()  # попытка 1 — в общем сообщении
            assert "перезапуск не помог" in bot.sent[-1]
            sent = len(bot.sent)
            for _ in range(MAX_ATTEMPTS):
                clock.t += max(BACKOFF)
                await job.tick()
            assert docker.restarted.count("flaresolverr") == MAX_ATTEMPTS
            assert len(bot.sent) == sent + 1 and "Авторемонт не помог" in bot.sent[-1] and "💡" in bot.sent[-1]
            clock.t += max(BACKOFF)
            await job.tick()
            assert len(bot.sent) == sent + 1  # «не помогло» — один раз

    asyncio.run(scenario())


def test_watch_job_leaves_containers_that_were_down_before_start_and_works_without_fixer():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            docker = Docker(PS)
            await store.set(DockerWatchJob.KEY, {"romm": "x", "romm-db": "x", "flaresolverr": "x", "homarr": "x"})
            job = DockerWatchJob(bot=Bot(), admin_id=1, docker=docker, store=store, ignore=set(), fixer=_fixer(docker))  # type: ignore[arg-type]
            await job.tick()
            assert docker.restarted == []  # уже стояли до запуска бота — только по кнопке
            plain = DockerWatchJob(bot=Bot(), admin_id=1, docker=Docker(PS), store=StateStore(Path(tmp) / "t.json"), ignore=set())  # type: ignore[arg-type]
            await plain.tick()  # без модуля — прежнее поведение, без автоперезапуска

    asyncio.run(scenario())


# ------------------------------------------------------------------ qBittorrent
class Qbit:
    def __init__(self, good=("admin", "new")):
        self.good = good
        self.username = "admin"
        self.base_url = "http://127.0.0.1:8080"
        self.login_problem = "auth"
        self.auth_failed = True
        self.creds = None
        self.real_url = "http://127.0.0.1:8080"

    async def check_login(self, username, password, base_url=None):
        if (base_url or self.base_url) != self.real_url:
            raise AppError("по адресу отвечает не qBittorrent — проверьте QBIT_URL")
        if (username, password) != self.good:
            raise AppError("неверный логин или пароль qBittorrent (QBIT_USERNAME/QBIT_PASSWORD)")

    def set_credentials(self, username, password, base_url=None):
        self.creds = (username, password)
        self.base_url = base_url or self.base_url
        self.auth_failed = False


def test_qbit_login_checks_first_then_writes_env_with_backup():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".env"
            path.write_text(ENV, encoding="utf-8")
            settings = _settings(root, qbit_username="admin", qbit_password="old")
            apps = SimpleNamespace(qbit=Qbit())
            try:
                await fixes.qbit_login(settings, apps, "admin", "wrong", path)  # type: ignore[arg-type]
            except AppError:
                pass
            else:
                raise AssertionError("неверный пароль сохранили")
            assert path.read_text(encoding="utf-8") == ENV and apps.qbit.creds is None
            saved = await fixes.qbit_login(settings, apps, "admin", "new", path)  # type: ignore[arg-type]
            assert saved.where == "env" and saved.backup is not None and apps.qbit.creds == ("admin", "new")
            assert "QBIT_PASSWORD=new\n" in path.read_text(encoding="utf-8") and "DIGEST_TIME" in path.read_text(encoding="utf-8")
            assert settings.qbit_password is not None and settings.qbit_password.get_secret_value() == "new"

    asyncio.run(scenario())


def test_qbit_login_without_env_rights_works_until_restart_and_says_why():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _settings(root, qbit_username="admin", qbit_password="old")
            apps = SimpleNamespace(qbit=Qbit())
            saved = await fixes.qbit_login(settings, apps, "admin", "new", root / "нет.env")  # type: ignore[arg-type]
            assert saved.where == "memory" and "update.sh" in saved.note and apps.qbit.creds == ("admin", "new")

    asyncio.run(scenario())


def test_doctor_offers_buttons_for_found_problems():
    from homebot.handlers import autofix as h

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = Path.cwd()
        os.chdir(root)
        try:
            Path(".env").write_text(ENV, encoding="utf-8")
            apps = SimpleNamespace(qbit=Qbit(), docker=object())
            found = asyncio.run(h.offers(_settings(root), apps, _fixer(Docker(PS)), True))  # type: ignore[arg-type]
        finally:
            os.chdir(old)
        assert [act for act, _ in found] == ["env", "dock", "qbit"]
        assert "(5)" in found[0][1]
        text = h.render_env(fixes.env_report(_settings(root), root / "нет.env"))
        assert "Все ключи знакомы" in text


# ------------------------------------------------------------------ панель
class _Req:
    def __init__(self, ctx, body=None):
        self.app = {"ctx": ctx}
        self._body = body
        self.query: dict[str, str] = {}
        self.match_info: dict[str, str] = {}

    async def json(self):
        return self._body


def test_panel_fixes_need_confirm_and_never_save_a_bad_qbit_password(tmp_path, monkeypatch):
    import pytest

    pytest.importorskip("aiohttp.web")
    from homebot.web import api

    monkeypatch.chdir(tmp_path)
    Path(".env").write_text(ENV, encoding="utf-8")
    settings = _settings(tmp_path, qbit_username="admin", qbit_password="old")
    docker = Docker(PS)
    ctx = SimpleNamespace(settings=settings, apps=SimpleNamespace(qbit=Qbit(), docker=docker), fixer=_fixer(docker))
    state = asyncio.run(api.control_fixes(_Req(ctx)))
    assert len(state["env"]["legacy"]) == 5 and state["docker"] and state["qbit"] and state["qbit_user"] == "admin"
    with pytest.raises(api.ApiError, match="confirm"):
        asyncio.run(api.control_fixes_post(_Req(ctx, {"op": "env_clean"})))
    done = asyncio.run(api.control_fixes_post(_Req(ctx, {"op": "env_clean", "confirm": True})))
    assert len(done["removed"]) == 5 and done["env"]["legacy"] == []
    with pytest.raises(api.ApiError, match="Ничего не сохранил"):
        asyncio.run(api.control_fixes_post(_Req(ctx, {"op": "qbit", "username": "admin", "password": "bad"})))
    assert "QBIT_PASSWORD=old" in Path(".env").read_text(encoding="utf-8")
    ok = asyncio.run(api.control_fixes_post(_Req(ctx, {"op": "qbit", "username": "admin", "password": "new"})))
    assert ok["where"] == "env" and "QBIT_PASSWORD=new" in Path(".env").read_text(encoding="utf-8")
    fixed = asyncio.run(api.control_fixes_post(_Req(ctx, {"op": "docker"})))
    assert {r["name"] for r in fixed["results"]} == {"romm", "romm-db", "flaresolverr", "homarr"}


def test_qbit_login_can_move_to_a_new_address():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".env"
            path.write_text(ENV, encoding="utf-8")
            settings = _settings(root, qbit_username="admin", qbit_password="old")
            qbit = Qbit()
            qbit.real_url = "http://192.168.0.165:8080"
            apps = SimpleNamespace(qbit=qbit)
            try:
                await fixes.qbit_login(settings, apps, "admin", "new", path)  # type: ignore[arg-type]
            except AppError as exc:
                assert "не qBittorrent" in str(exc)
            try:
                await fixes.qbit_login(settings, apps, "admin", "new", path, url="ftp:/x")  # type: ignore[arg-type]
            except ValueError:
                pass
            else:
                raise AssertionError("кривой адрес приняли")
            saved = await fixes.qbit_login(settings, apps, "admin", "new", path, url="http://192.168.0.165:8080/")  # type: ignore[arg-type]
            text = path.read_text(encoding="utf-8")
            assert (
                saved.where == "env"
                and "QBIT_URL=http://192.168.0.165:8080\n" in text
                and qbit.base_url == "http://192.168.0.165:8080"
            )
            assert settings.qbit_url == "http://192.168.0.165:8080"

    asyncio.run(scenario())


class _Resp:
    def __init__(self, status, body):
        self.status, self._body, self.cookies = status, body, {}

    async def text(self):
        return self._body

    async def read(self):
        return self._body.encode()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Session:
    def __init__(self, login, page):
        self.login, self.page = login, page

    def post(self, url, **kw):
        return _Resp(*self.login)

    def get(self, url, **kw):
        return _Resp(*self.page)


def test_qbit_tells_wrong_address_from_wrong_password():
    from homebot.services.qbittorrent import QbitService

    async def problem(login, page):
        q = QbitService(_Session(login, page), "http://127.0.0.1:8080", "admin", "x")  # type: ignore[arg-type]
        try:
            await q.transfer()
        except AppError as exc:
            return q.login_problem, str(exc)
        raise AssertionError("вход прошёл")

    kind, text = asyncio.run(problem((401, "Unauthorized"), (200, "<title>Homarr</title>")))
    assert kind == "address" and "QBIT_URL" in text
    kind, _ = asyncio.run(problem((401, ""), (200, "<title>qBittorrent WebUI</title>")))
    assert kind == "auth"
    kind, _ = asyncio.run(problem((200, "Fails."), (200, "<html>qBittorrent</html>")))
    assert kind == "auth"
    kind, _ = asyncio.run(problem((404, "Not found"), (200, "")))
    assert kind == "address"
