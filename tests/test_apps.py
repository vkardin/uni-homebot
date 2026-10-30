import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import aiohttp

from homebot.jobs.apps_watch import DockerWatchJob, SpeedtestWatchJob, TorrentWatchJob
from homebot.services.adguard import AdGuardService, parse_adguard
from homebot.services.apps_http import AppError, basic_auth
from homebot.services.charts import render_speed_chart
from homebot.services.docker_api import NAME_RE, Container, DockerService, demux_logs, parse_containers
from homebot.services.jellyfin import parse_items, parse_sessions
from homebot.services.qbittorrent import QbitService, parse_torrents
from homebot.services.speedtest import SpeedtestService, parse_result
from homebot.services.state import StateStore
from homebot.ui.texts import (
    render_adguard,
    render_container,
    render_docker,
    render_jellyfin,
    render_qbit,
    render_speedtest,
)
from tests.fakes import FakeResponse, FakeSession

KYIV = ZoneInfo("Europe/Kyiv")

# ----------------------------------------------------------------------- Docker
DOCKER_PS = [
    {
        "Names": ["/jellyfin"],
        "Image": "jellyfin/jellyfin:latest",
        "State": "running",
        "Status": "Up 22 hours (healthy)",
    },
    {"Names": ["/romm"], "Image": "rommapp/romm:latest", "State": "exited", "Status": "Exited (137) 6 hours ago"},
    {"Names": ["/homarr"], "Image": "ghcr.io/ajnart/homarr", "State": "running", "Status": "Up 22 hours (unhealthy)"},
    {"Names": ["/romm-db"], "Image": "mariadb", "State": "exited", "Status": "Exited (0) 6 hours ago"},
    {"Names": ["/dashdot"], "Image": "dashdot", "State": "running", "Status": "Up 22 hours"},
    "junk",
]


def test_parse_containers_problems_first():
    containers = parse_containers(DOCKER_PS)
    assert [c.name for c in containers] == ["homarr", "romm", "romm-db", "dashdot", "jellyfin"]
    by_name = {c.name: c for c in containers}
    assert "137" in by_name["romm"].problem and "памяти" in by_name["romm"].problem
    assert by_name["romm-db"].problem == "остановлен (код 0)"
    assert "unhealthy" in by_name["homarr"].problem and by_name["homarr"].emoji == "🟠"
    assert by_name["jellyfin"].problem is None and by_name["jellyfin"].health == "healthy"
    assert by_name["dashdot"].health is None and by_name["dashdot"].emoji == "🟢"
    text = render_docker(containers)
    assert "запущено 3 из 5" in text and "romm" in text


def test_demux_logs():
    frames = b"\x01\x00\x00\x00\x00\x00\x00\x06hello\n" + b"\x02\x00\x00\x00\x00\x00\x00\x05oops\n"
    assert demux_logs(frames) == "hello\noops\n"
    assert demux_logs(b"plain tty output\n") == "plain tty output\n"


def test_container_name_validation():
    for good in ("romm", "romm-db", "nginx-proxy-manager", "a.b_c"):
        assert NAME_RE.fullmatch(good)
    for bad in ("", "-rm", "a b", "x;rm", "../etc", "a/b", "x" * 80):
        assert not NAME_RE.fullmatch(bad), bad


def test_docker_service_uses_read_only_calls_and_rejects_bad_names():
    calls = []

    def handler(method, url, kwargs):
        calls.append((method, url, kwargs.get("params")))
        if url.endswith("/containers/json"):
            return FakeResponse(body=DOCKER_PS)
        return FakeResponse(raw=b"\x01\x00\x00\x00\x00\x00\x00\x03ok\n")

    docker = DockerService(FakeSession(handler), "http://127.0.0.1:2375/", restart_enabled=False)
    assert len(asyncio.run(docker.containers())) == 5
    assert asyncio.run(docker.logs("romm")) == "ok"
    assert all(method == "GET" for method, _, _ in calls)  # никаких POST в прокси
    assert calls[0][2] == {"all": "true"} and calls[1][1] == "http://127.0.0.1:2375/containers/romm/logs"
    try:
        asyncio.run(docker.logs("../../etc"))
    except AppError:
        pass
    else:
        raise AssertionError("AppError expected")
    ok, message = asyncio.run(docker.restart("romm"))
    assert not ok and "DOCKER_RESTART_ENABLED" in message
    ok, message = asyncio.run(DockerService(FakeSession(handler), "http://x", restart_enabled=True).restart("a;b"))
    assert not ok and "недопустимое" in message


def test_container_card_fits_telegram():
    container = Container("romm", "rommapp/romm:latest", "exited", "Exited (137) 6 hours ago")
    text = render_container(container, "<строка>\n" * 2000, None)
    assert len(text) <= 4096 and "&lt;строка&gt;" in text and "137" in text
    assert "недоступен" in render_container(container, None, "сервис недоступен")


def test_connection_errors_become_app_errors():
    def down(*a):
        raise aiohttp.ClientConnectionError("refused")

    try:
        asyncio.run(DockerService(FakeSession(down), "http://x", restart_enabled=False).containers())
    except AppError as exc:
        assert "недоступен" in str(exc)
    else:
        raise AssertionError("AppError expected")
    try:
        asyncio.run(DockerService(FakeSession(lambda *a: FakeResponse(status=403)), "http://x", restart_enabled=False).containers())
    except AppError as exc:
        assert "403" in str(exc)
    else:
        raise AssertionError("AppError expected")


# -------------------------------------------------------------------- Speedtest
RESULT = {
    "id": 42,
    "status": "completed",
    "ping": 12.4,
    "download": 12_500_000,  # байт/с = 100 Мбит/с
    "upload": 2_500_000,
    "download_bits": 100_000_000,
    "upload_bits": 20_000_000,
    "healthy": True,
    "created_at": "2026-10-13T08:00:00.000000Z",
    "data": {"isp": "Kyivstar", "server": {"name": "Dnipro"}},
}


def test_parse_speed_result_units():
    r = parse_result(RESULT)
    assert (r.download_mbps, r.upload_mbps, r.ping_ms) == (100.0, 20.0, 12.4)
    assert r.ok and r.isp == "Kyivstar" and r.server == "Dnipro" and r.created_at.tzinfo is not None
    bytes_only = parse_result({"id": 1, "status": "completed", "download": 12_500_000, "created_at": "2026-10-13 08:00:00"})
    assert bytes_only.download_mbps == 100.0 and bytes_only.upload_mbps is None
    assert not parse_result({"id": 2, "status": "failed"}).ok


def test_speedtest_service_auth_wrapping_and_404():
    seen = []

    def handler(method, url, kwargs):
        seen.append((method, url, kwargs["headers"], kwargs.get("params")))
        if url.endswith("/results/latest"):
            return FakeResponse(body={"data": RESULT})  # в обёртке
        if url.endswith("/results"):
            return FakeResponse(body={"data": [RESULT, {**RESULT, "id": 41, "created_at": "2026-10-12T08:00:00Z"}, "junk"]})
        return FakeResponse(status=201, body={"message": "queued"})

    service = SpeedtestService(FakeSession(handler), "http://127.0.0.1:8084/", "tok")
    assert asyncio.run(service.latest()).id == 42
    history = asyncio.run(service.history(7, now=datetime(2026, 10, 13, tzinfo=UTC)))
    assert [r.id for r in history] == [41, 42]  # по времени
    asyncio.run(service.run())
    assert seen[0][2]["Authorization"] == "Bearer tok" and seen[0][2]["Accept"] == "application/json"
    assert seen[1][3]["filter[start_at]"] == "2026-10-06"
    assert seen[2][:2] == ("POST", "http://127.0.0.1:8084/api/v1/speedtests/run")

    empty = SpeedtestService(FakeSession(lambda *a: FakeResponse(status=404)), "http://x", "t")
    assert asyncio.run(empty.latest()) is None


def test_speedtest_latest_falls_back_to_list_on_http_500():
    def handler(method, url, kwargs):
        if url.endswith("/results/latest"):
            return FakeResponse(status=500)
        return FakeResponse(body={"data": [{"id": 7, "status": "failed"}, {**RESULT, "id": 9}, "junk"]})

    service = SpeedtestService(FakeSession(handler), "http://x", "t")
    assert asyncio.run(service.latest()).id == 9  # последний по номеру

    def broken(method, url, kwargs):
        return FakeResponse(status=500)

    try:
        asyncio.run(SpeedtestService(FakeSession(broken), "http://x", "t").latest())
    except AppError as exc:
        assert "500" in str(exc)
    else:
        raise AssertionError("AppError expected")


def test_speedtest_history_retries_without_filters_on_500():
    def handler(method, url, kwargs):
        if "filter[start_at]" in (kwargs.get("params") or {}):
            return FakeResponse(status=500)
        old = {**RESULT, "id": 1, "created_at": "2026-09-01T08:00:00Z"}
        return FakeResponse(body={"data": [old, RESULT]})

    service = SpeedtestService(FakeSession(handler), "http://x", "t")
    history = asyncio.run(service.history(7, now=datetime(2026, 10, 13, tzinfo=UTC)))
    assert [r.id for r in history] == [42]  # старый замер отрезан на нашей стороне


def test_speedtest_render_and_chart():
    good = parse_result(RESULT)
    slow = parse_result({**RESULT, "id": 43, "download_bits": 30_000_000})
    failed = parse_result({"id": 44, "status": "failed", "created_at": "2026-10-13T09:00:00Z"})
    text = render_speedtest(good, [good, slow, failed], KYIV)
    assert "<b>100</b>" in text and "мин 30" in text and "Неудачных замеров: 1" in text and "11:00" in text
    assert "ещё не было" in render_speedtest(None, [], KYIV)
    png = render_speed_chart(
        [(good.created_at, 100.0, 20.0), (datetime(2026, 10, 13, 10, tzinfo=UTC), 90.0, 19.0)], tz=KYIV, title="t"
    )
    assert png[:4] == b"\x89PNG"


# ------------------------------------------------------------------- qBittorrent
TORRENTS = [
    {
        "hash": "a",
        "name": "Movie.2026.1080p",
        "progress": 0.42,
        "state": "downloading",
        "dlspeed": 5_000_000,
        "eta": 600,
        "size": 4 * 1024**3,
    },
    {"hash": "b", "name": "Series S01", "progress": 1.0, "state": "stalledUP", "size": 10 * 1024**3},
    {"hash": "c", "name": "Paused thing", "progress": 0.1, "state": "stoppedDL", "size": 1},
    {"no_hash": True},
]


def _cookie(value):
    return SimpleNamespace(value=value)


def test_qbit_login_relogin_and_v5_fallback():
    log = []
    state = {"logins": 0, "expired_once": False}

    def handler(method, url, kwargs):
        path = url.split("8080")[1]
        log.append((method, path, kwargs.get("headers", {}).get("Cookie")))
        if path == "/api/v2/auth/login":
            state["logins"] += 1
            assert kwargs["data"] == {"username": "admin", "password": "pw"}
            response = FakeResponse(raw=b"Ok.")
            response.cookies = {"SID": _cookie(f"sid{state['logins']}")}
            return response
        if path == "/api/v2/torrents/info" and not state["expired_once"]:
            state["expired_once"] = True
            return FakeResponse(status=403)  # сессия истекла
        if path == "/api/v2/torrents/stop":
            return FakeResponse(status=404)  # qBittorrent 4.x
        if path == "/api/v2/torrents/info":
            return FakeResponse(body=TORRENTS)
        return FakeResponse(raw=b"")

    qbit = QbitService(FakeSession(handler), "http://127.0.0.1:8080", "admin", "pw")
    torrents = asyncio.run(qbit.torrents())
    assert len(torrents) == 3 and state["logins"] == 2  # перелогин после 403
    asyncio.run(qbit.pause_all())
    paths = [p for _, p, _ in log]
    assert paths[-2:] == ["/api/v2/torrents/stop", "/api/v2/torrents/pause"]
    assert log[-1][2] == "SID=sid2"


def test_qbit_bad_password():
    def handler(method, url, kwargs):
        return FakeResponse(raw=b"Fails.")

    try:
        asyncio.run(QbitService(FakeSession(handler), "http://q", "a", "b").transfer())
    except AppError as exc:
        assert "логин или пароль" in str(exc)
    else:
        raise AssertionError("AppError expected")


def test_qbit_52_login_204_ok_and_401_bad_password():
    state = {"ok": True}

    def handler(method, url, kwargs):
        if url.endswith("/api/v2/auth/login"):
            if not state["ok"]:
                return FakeResponse(status=401, raw=b"")
            response = FakeResponse(status=204, raw=b"")
            response.cookies = {"SID": _cookie("sid-new")}
            return response
        return FakeResponse(body={"dl_info_speed": 5, "up_info_speed": 1, "dl_info_data": 2, "up_info_data": 3})

    assert asyncio.run(QbitService(FakeSession(handler), "http://q", "a", "b").transfer()).dl_speed == 5
    state["ok"] = False
    try:
        asyncio.run(QbitService(FakeSession(handler), "http://q", "a", "b").transfer())
    except AppError as exc:
        assert "логин или пароль" in str(exc)
    else:
        raise AssertionError("AppError expected")


def test_qbit_render():
    from homebot.services.qbittorrent import Transfer

    text = render_qbit(Transfer(5_000_000, 100_000, 10**9, 10**8), parse_torrents(TORRENTS))
    assert "Movie.2026.1080p" in text and "42%" in text and "Загружается: 2" in text and "готово: 1" in text
    assert "Series" not in text  # завершённые не показываем среди активных


# ---------------------------------------------------------------------- AdGuard
STATUS = {"protection_enabled": True, "protection_disabled_duration": 0, "version": "v0.107.55", "running": True}
STATS = {
    "num_dns_queries": 12000,
    "num_blocked_filtering": 1800,
    "avg_processing_time": 0.012,
    "top_blocked_domains": [{"ads.example.com": 500}, {"track.example.net": 900}, {"x": "bad"}],
}


def test_adguard_parse_render_and_protection_body():
    stats = parse_adguard(STATUS, STATS)
    assert stats.blocked_percent == 15.0 and round(stats.avg_ms) == 12
    assert stats.top_blocked[0] == ("track.example.net", 900)
    assert "15.0%" in render_adguard(stats) and "12 000" in render_adguard(stats)
    off = parse_adguard({**STATUS, "protection_enabled": False, "protection_disabled_duration": 300_000}, STATS)
    assert "5м" in render_adguard(off)

    bodies = []

    def handler(method, url, kwargs):
        bodies.append((url, kwargs.get("json"), kwargs["headers"]["Authorization"]))
        return FakeResponse(raw=b"")

    service = AdGuardService(FakeSession(handler), "http://127.0.0.1:8880", "admin", "secret")
    asyncio.run(service.set_protection(False, minutes=10))
    asyncio.run(service.set_protection(True))
    assert bodies[0][0] == "http://127.0.0.1:8880/control/protection"
    assert bodies[0][1] == {"enabled": False, "duration": 600_000}
    assert bodies[1][1] == {"enabled": True}
    assert bodies[0][2] == basic_auth("admin", "secret") == "Basic YWRtaW46c2VjcmV0"


# --------------------------------------------------------------------- Jellyfin
def test_jellyfin_parse_and_render():
    sessions = [
        {"UserName": "patrik", "DeviceName": "TV", "NowPlayingItem": {"Name": "Pilot", "SeriesName": "Show",
         "ParentIndexNumber": 1, "IndexNumber": 2, "RunTimeTicks": 1000}, "PlayState": {"PositionTicks": 250, "IsPaused": True}},
        {"UserName": "idle", "DeviceName": "Phone"},
    ]  # fmt: skip
    playing = parse_sessions(sessions)
    assert len(playing) == 1 and playing[0].title == "Show S01E02 — Pilot" and playing[0].progress == 25.0
    items = parse_items({"Items": [{"Name": "Dune", "Type": "Movie", "ProductionYear": 2021}, "junk"]})
    assert items[0].year == 2021
    text = render_jellyfin(playing, items)
    assert "patrik" in text and "25%" in text and "⏸" in text and "Dune (2021)" in text


# ------------------------------------------------------------------- фоновые задачи
class FakeBot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id, text):
        self.sent.append(text)


class FakeDocker:
    def __init__(self, data):
        self.data = data

    async def containers(self):
        return parse_containers(self.data)

    async def logs(self, name, tail=60):
        return ""


def test_docker_watch_new_problems_recovery_and_ignore():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            bot = FakeBot()
            docker = FakeDocker(DOCKER_PS)
            job = DockerWatchJob(bot=bot, admin_id=1, docker=docker, store=store, ignore={"romm-db"})  # type: ignore[arg-type]
            await job.tick()
            await job.tick()
            assert len(bot.sent) == 1
            assert "romm" in bot.sent[0] and "homarr" in bot.sent[0] and "romm-db" not in bot.sent[0]

            fixed = [dict(d) for d in DOCKER_PS if isinstance(d, dict)]
            fixed[1].update(State="running", Status="Up 1 minute")
            docker.data = fixed
            await job.tick()
            assert len(bot.sent) == 2 and "снова в порядке" in bot.sent[1] and "romm" in bot.sent[1]

            restarted = DockerWatchJob(bot=bot, admin_id=1, docker=docker, store=store, ignore={"romm-db"})  # type: ignore[arg-type]
            await restarted.tick()
            assert len(bot.sent) == 2  # после перезапуска бота — без повторов

    asyncio.run(scenario())


class FakeSpeed:
    def __init__(self) -> None:
        self.result = None

    async def latest(self):
        return self.result


def test_speedtest_watch_baseline_failure_and_threshold():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            bot, speed = FakeBot(), FakeSpeed()
            job = SpeedtestWatchJob(bot=bot, admin_id=1, speedtest=speed, store=store, min_down=50)  # type: ignore[arg-type]
            speed.result = parse_result({**RESULT, "id": 1, "download_bits": 10_000_000})
            await job.tick()
            assert bot.sent == []  # первый запуск — только запоминаем
            speed.result = parse_result({**RESULT, "id": 2})
            await job.tick()
            assert bot.sent == []  # 100 Мбит/с — норма
            speed.result = parse_result({**RESULT, "id": 3, "download_bits": 20_000_000})
            await job.tick()
            await job.tick()
            assert len(bot.sent) == 1 and "20" in bot.sent[0]
            speed.result = parse_result({"id": 4, "status": "failed"})
            await job.tick()
            assert len(bot.sent) == 1  # одиночный сбой — без тревоги
            speed.result = parse_result({"id": 5, "status": "failed", "data": {"message": "Server unreachable"}})
            await job.tick()
            await job.tick()
            assert len(bot.sent) == 2 and "не удались" in bot.sent[1] and "Server unreachable" in bot.sent[1]
            speed.result = parse_result({"id": 6, "status": "failed"})
            await job.tick()
            assert len(bot.sent) == 2  # не повторяем
            speed.result = parse_result({"id": 7, "status": "started"})
            await job.tick()
            assert len(bot.sent) == 2  # идущий замер не трогаем
            speed.result = parse_result({**RESULT, "id": 8})
            await job.tick()
            assert len(bot.sent) == 3 and "снова проходят" in bot.sent[2]

    asyncio.run(scenario())


class FakeQbit:
    def __init__(self, data):
        self.data = data

    async def torrents(self):
        return parse_torrents(self.data)


def test_torrent_watch_reports_each_completion_once():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            bot, qbit = FakeBot(), FakeQbit(TORRENTS)
            job = TorrentWatchJob(bot=bot, admin_id=1, qbit=qbit, store=store)  # type: ignore[arg-type]
            await job.tick()
            assert bot.sent == []  # уже скачанное до запуска бота — не новость
            data = [dict(t) for t in TORRENTS]
            data[0]["progress"] = 1.0
            qbit.data = data
            await job.tick()
            await job.tick()
            assert len(bot.sent) == 1 and "Movie.2026.1080p" in bot.sent[0] and "4.0 ГБ" in bot.sent[0]

    asyncio.run(scenario())


def test_apps_hub_text_knows_every_connected_app():
    from homebot.services.apps import Apps
    from homebot.ui.texts import render_apps

    names = ("docker", "speedtest", "qbit", "adguard", "jellyfin", "radarr", "sonarr", "actual")
    text = render_apps(list(names))  # раньше «actual» ронял экран «Приложения»
    assert "Actual Budget" in text and "Docker" in text
    assert "что-то новое" in render_apps(["что-то новое"])
    assert set(Apps().available()) <= set(names)


def test_qbit_does_not_hammer_login_after_refusal():
    posts = []

    def handler(method, url, kwargs):
        posts.append(url)
        return FakeResponse(status=403)  # IP забанен

    qbit = QbitService(FakeSession(handler), "http://q", "a", "b")
    for _ in range(3):
        try:
            asyncio.run(qbit.transfer())
        except AppError as exc:
            assert "заблокировал" in str(exc)
    assert len(posts) == 1  # вход пробовали один раз, дальше — пауза без обращений к qBittorrent
