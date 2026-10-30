"""2.6: самолечение контейнеров, просадка скорости, недельный отчёт."""

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError

from homebot.jobs.apps_watch import DockerWatchJob, SpeedtestWatchJob
from homebot.services.apps_http import AppError
from homebot.services.docker_api import parse_containers
from homebot.services.docker_diag import prepare_diag_input, render_tail, tail_lines, tidy_diag
from homebot.services.speed_analysis import assess, baseline_of, period_stats, split_weeks
from homebot.services.speedtest import SpeedResult
from homebot.services.state import StateStore
from homebot.ui.extra_texts import render_speed_drop, render_speed_recovered, render_speed_week

KYIV = ZoneInfo("Europe/Kyiv")
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def result(i, down, *, status="completed", up=20.0, ping=10.0, when=None):
    return SpeedResult(
        id=i, created_at=when or T0 + timedelta(hours=i), status=status,
        download_mbps=down, upload_mbps=up, ping_ms=ping,
    )  # fmt: skip


def series(downs, start=1):
    return [result(start + n, d) for n, d in enumerate(downs)]


# ------------------------------------------------------------------ анализ скорости
def test_baseline_excludes_recent_and_needs_enough_samples():
    history = series([100.0] * 10 + [10.0, 10.0, 10.0])
    assert baseline_of(history) == 100.0  # просадка не «нормализуется»
    assert baseline_of(series([100.0] * 5)) is None  # мало замеров
    assert baseline_of(series([2.0] * 20)) is None  # слишком медленная линия — относительные проценты бессмысленны
    failed = [*series([100.0] * 11), result(99, None, status="failed")]
    assert baseline_of(failed) == 100.0


def test_assess_degraded_recovered_and_unknown():
    normal = [100.0] * 10
    verdict, drop = assess(series([*normal, 40.0, 30.0, 45.0]), drop_percent=50)
    assert verdict == "degraded" and drop is not None
    assert drop.baseline == 100.0 and 38 < drop.recent < 39 and drop.share < 0.5

    verdict, _ = assess(series([*normal, 40.0, 30.0, 60.0]), drop_percent=50)
    assert verdict == "unknown"  # один замер выше порога — не просадка

    verdict, drop = assess(series([*normal, 30.0, 40.0, 95.0]), drop_percent=50)
    assert verdict == "recovered" and drop is not None

    assert assess(series([*normal, 10.0, 10.0, 10.0]), drop_percent=0)[0] == "unknown"  # выключено
    assert assess(series([100.0, 100.0, 5.0, 5.0, 5.0]), drop_percent=50)[0] == "unknown"  # нет «обычной» скорости
    verdict, _ = assess(series([*normal, 60.0, 70.0, 75.0]), drop_percent=30)
    assert verdict == "unknown"  # порог 70: 75 его не ниже — не просадка


def test_period_stats_and_week_split():
    now = T0 + timedelta(days=20)
    this = [result(i, 100.0 + i, when=now - timedelta(days=1, hours=i)) for i in range(10)]
    prev = [result(50 + i, 80.0, when=now - timedelta(days=8, hours=i)) for i in range(5)]
    old = [result(90, 1.0, when=now - timedelta(days=30))]
    fail = [result(95, None, status="failed", when=now - timedelta(hours=2))]
    a, b = split_weeks([*old, *prev, *this, *fail], now)
    assert a is not None and b is not None
    assert (a.count, a.failed, b.count) == (10, 1, 5)
    assert 100 <= a.down_min <= 101 and a.down_p10 <= a.down_median <= a.down_avg + 5
    assert period_stats([]) is None and period_stats(fail) is None


def test_speed_texts():
    _, drop = assess(series([*[100.0] * 10, 20.0, 25.0, 30.0]), drop_percent=50)
    assert "просел" in render_speed_drop(drop) and "25 % от обычной" in render_speed_drop(drop)
    assert "вернулась" in render_speed_recovered(drop)
    this = period_stats(series([100.0] * 5))
    prev = period_stats(series([50.0] * 5))
    assert this is not None
    text = render_speed_week(this, prev)
    assert "+100 % к прошлой неделе" in text and "обычно <b>100</b>" in text
    assert "как на прошлой неделе" in render_speed_week(this, this)
    assert "прошлой" not in render_speed_week(this, None)


# ----------------------------------------------------------------- Speedtest-задача
class FakeBot:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.markups: list[object] = []
        self.edits: list[tuple[int, str]] = []
        self.fail = False

    async def send_message(self, chat_id, text, reply_markup=None):
        if self.fail:
            raise TelegramAPIError(method=None, message="offline")  # type: ignore[arg-type]
        self.sent.append(text)
        self.markups.append(reply_markup)
        return SimpleNamespace(message_id=len(self.sent))

    async def edit_message_text(self, *, text, chat_id, message_id, reply_markup=None):
        self.edits.append((message_id, text))


class FakeSpeed:
    def __init__(self, history):
        self.history_data = history
        self.fail_history = False

    async def latest(self):
        return self.history_data[-1] if self.history_data else None

    async def history(self, days=7, *, now=None):
        if self.fail_history:
            raise AppError("нет истории")
        return list(self.history_data)


def test_speedtest_relative_drop_alerts_once_and_recovery_once():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            bot = FakeBot()
            history = series([100.0] * 10)
            speed = FakeSpeed(history)
            job = SpeedtestWatchJob(bot=bot, admin_id=1, speedtest=speed, store=store, min_down=0, drop_percent=50)  # type: ignore[arg-type]
            await job.tick()  # первый запуск — запоминаем
            assert bot.sent == []
            for n, value in enumerate([30.0, 35.0], start=11):
                speed.history_data.append(result(n, value))
                await job.tick()
                assert bot.sent == []  # пока только 2 из 3 подряд
            speed.history_data.append(result(13, 20.0))
            await job.tick()
            assert len(bot.sent) == 1 and "просел" in bot.sent[0]
            speed.history_data.append(result(14, 25.0))
            await job.tick()
            assert len(bot.sent) == 1  # повторно не шумим
            speed.history_data.extend([result(15, 90.0)])
            await job.tick()
            assert len(bot.sent) == 2 and "вернулась" in bot.sent[1]
            speed.history_data.append(result(16, 95.0))
            await job.tick()
            assert len(bot.sent) == 2

    asyncio.run(scenario())


def test_speedtest_relative_retries_when_delivery_failed_and_survives_missing_history():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            bot = FakeBot()
            speed = FakeSpeed(series([100.0] * 10))
            job = SpeedtestWatchJob(bot=bot, admin_id=1, speedtest=speed, store=store, min_down=0, drop_percent=50)  # type: ignore[arg-type]
            await job.tick()
            speed.history_data.extend(series([20.0, 20.0, 20.0], start=11))
            bot.fail = True
            await job.tick()
            assert bot.sent == []
            bot.fail = False
            await job.tick()  # сообщение не потеряно
            assert len(bot.sent) == 1 and "просел" in bot.sent[0]

            speed.fail_history = True
            speed.history_data.append(result(20, 10.0))
            await job.tick()  # история недоступна — задача не падает
            assert len(bot.sent) == 1

    asyncio.run(scenario())


def test_speedtest_weekly_report_sunday_evening_once_with_chart_or_text():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            bot = FakeBot()
            sunday = datetime(2026, 10, 11, 20, 30, tzinfo=KYIV)  # воскресенье
            assert sunday.weekday() == 6
            history = [result(i, 100.0, when=sunday - timedelta(hours=i * 6)) for i in range(1, 25)]
            speed = FakeSpeed(history)
            photos: list[tuple[bytes, str]] = []

            async def chart(points):
                assert points and all(len(p) == 3 for p in points)
                return b"PNG"

            async def photo(png, caption):
                photos.append((png, caption))
                return True

            job = SpeedtestWatchJob(
                bot=bot, admin_id=1, speedtest=speed, store=store, min_down=0,  # type: ignore[arg-type]
                tz=KYIV, weekly=True, chart=chart, photo=photo,
            )  # fmt: skip
            assert not await job.weekly_report(sunday.replace(hour=19))  # рано
            assert not await job.weekly_report(sunday - timedelta(days=1, hours=-1))  # не воскресенье
            assert await job.weekly_report(sunday)
            assert photos and photos[0][0] == b"PNG" and "Интернет за неделю" in photos[0][1]
            assert not await job.weekly_report(sunday + timedelta(minutes=10))  # уже отправляли на этой неделе
            assert bot.sent == []

            async def broken_photo(png, caption):
                return False

            store2 = StateStore(Path(tmp) / "s2.json")
            job2 = SpeedtestWatchJob(
                bot=bot, admin_id=1, speedtest=speed, store=store2, min_down=0,  # type: ignore[arg-type]
                tz=KYIV, weekly=True, chart=chart, photo=broken_photo,
            )  # fmt: skip
            assert await job2.weekly_report(sunday)  # фото не ушло — текстом
            assert len(bot.sent) == 1 and "Интернет за неделю" in bot.sent[0]

            empty = SpeedtestWatchJob(
                bot=bot, admin_id=1, speedtest=FakeSpeed([]), store=StateStore(Path(tmp) / "s3.json"), min_down=0,  # type: ignore[arg-type]
                tz=KYIV, weekly=True,
            )  # fmt: skip
            assert not await empty.weekly_report(sunday)  # нет замеров — молчим и пробуем позже

    asyncio.run(scenario())


# ----------------------------------------------------------------- Docker-задача
PS_BROKEN = [
    {"Names": ["/jellyfin"], "Image": "j", "State": "running", "Status": "Up 2 hours (healthy)"},
    {"Names": ["/romm"], "Image": "r", "State": "exited", "Status": "Exited (137) 6 hours ago"},
    {"Names": ["/homarr"], "Image": "h", "State": "running", "Status": "Up 1 hour (unhealthy)"},
]
LOGS = "\x1b[31mERROR\x1b[0m db connection refused <host>\nline2\n\nline3"


class FakeDocker:
    def __init__(self, data, *, restart_enabled=True):
        self.data = data
        self.restart_enabled = restart_enabled
        self.restarted: list[str] = []
        self.logs_ok = True

    async def containers(self):
        return parse_containers(self.data)

    async def logs(self, name, tail=60):
        if not self.logs_ok:
            raise AppError("журнал недоступен")
        return LOGS

    async def restart(self, name):
        self.restarted.append(name)
        return True, "перезапущен"


class FakeAsker:
    def __init__(self, answer="Не хватает памяти. Увеличьте лимит.", fail=False):
        self.answer, self.fail, self.calls = answer, fail, []

    async def ask(self, system, text, *, model=None, schema=None):
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("модель занята")
        return self.answer


async def _drain(job):
    await asyncio.gather(*list(job._background))


def test_docker_alert_has_log_tail_buttons_and_late_diagnosis():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            bot, docker, asker = FakeBot(), FakeDocker(PS_BROKEN), FakeAsker()
            markups: list[tuple[list[str], bool]] = []

            def markup(names, restart):
                markups.append((list(names), restart))
                return "KB"

            job = DockerWatchJob(
                bot=bot, admin_id=1, docker=docker, store=store, ignore=set(),  # type: ignore[arg-type]
                asker=asker, markup=markup,
            )  # fmt: skip
            await job.tick()
            assert len(bot.sent) == 1 and bot.markups == ["KB"]
            assert markups == [(["homarr", "romm"], True)]
            text = bot.sent[0]
            assert "<pre>" in text and "&lt;host&gt;" in text and "\x1b" not in text and "ERROR db connection" in text
            await _drain(job)
            assert len(bot.edits) == 1 and bot.edits[0][0] == 1
            assert text in bot.edits[0][1] and "🧠" in bot.edits[0][1] and "Не хватает памяти" in bot.edits[0][1]
            assert "Контейнер: homarr" in asker.calls[0] and "ERROR" in asker.calls[0]

            await job.tick()
            assert len(bot.sent) == 1  # повторов нет

    asyncio.run(scenario())


def test_docker_alert_survives_broken_logs_asker_and_delivery():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            bot, docker = FakeBot(), FakeDocker(PS_BROKEN)
            docker.logs_ok = False
            job = DockerWatchJob(bot=bot, admin_id=1, docker=docker, store=store, ignore=set(), asker=FakeAsker())  # type: ignore[arg-type]
            bot.fail = True
            await job.tick()
            assert bot.sent == [] and store.get(DockerWatchJob.KEY) is None  # не доставили — повторим
            bot.fail = False
            await job.tick()
            assert len(bot.sent) == 1 and "<pre>" not in bot.sent[0] and bot.markups == [None]
            await _drain(job)
            assert bot.edits == []  # журнала нет — разбирать нечего

            docker2 = FakeDocker(PS_BROKEN)
            bot2 = FakeBot()
            job2 = DockerWatchJob(
                bot=bot2, admin_id=1, docker=docker2, store=StateStore(Path(tmp) / "s2.json"), ignore=set(),  # type: ignore[arg-type]
                asker=FakeAsker(fail=True),
            )  # fmt: skip
            await job2.tick()
            await _drain(job2)
            assert len(bot2.sent) == 1 and bot2.edits == []  # модель упала — сообщение остаётся как было

    asyncio.run(scenario())


def test_docker_autoheal_restarts_listed_containers_with_hourly_limit():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            bot, docker = FakeBot(), FakeDocker(PS_BROKEN)
            job = DockerWatchJob(
                bot=bot, admin_id=1, docker=docker, store=store, ignore=set(), autoheal={"romm"},  # type: ignore[arg-type]
            )  # fmt: skip
            await job.tick()
            assert docker.restarted == ["romm"]  # homarr не в списке — только сообщение
            assert "перезапустил автоматически" in bot.sent[0]

            fixed = [dict(d) for d in PS_BROKEN]
            fixed[1].update(State="running", Status="Up 1 second")
            docker.data = fixed
            await job.tick()
            assert "снова в порядке" in bot.sent[1]

            for _ in range(DockerWatchJob.HEAL_LIMIT):  # падает снова и снова
                docker.data = PS_BROKEN
                await job.tick()
                docker.data = fixed
                await job.tick()
            assert len(docker.restarted) == DockerWatchJob.HEAL_LIMIT  # лимит в час соблюдён
            assert any("автоперезапуск исчерпан" in m for m in bot.sent)

            no_restart = FakeDocker(PS_BROKEN, restart_enabled=False)
            job3 = DockerWatchJob(
                bot=FakeBot(), admin_id=1, docker=no_restart, store=StateStore(Path(tmp) / "s3.json"), ignore=set(),  # type: ignore[arg-type]
                autoheal={"romm"},
            )  # fmt: skip
            await job3.tick()
            assert no_restart.restarted == []  # перезапуск отключён настройкой

    asyncio.run(scenario())


def test_docker_diag_helpers():
    raw = "\x1b[1;31mfail\x1b[0m\n" + "\n".join(f"line {i}" for i in range(20)) + "\n" + "x" * 400
    tail = tail_lines(raw, lines=3, width=50)
    assert tail.splitlines()[0] == "line 18" and tail.splitlines()[-1].endswith("…") and len(tail.splitlines()[-1]) == 50
    assert render_tail("") == "" and render_tail("a<b").startswith("<pre>a&lt;b")
    assert "Контейнер: romm" in prepare_diag_input("romm", "упал", "log", max_chars=10)
    assert prepare_diag_input("r", "p", "z" * 5000, max_chars=100).count("z") == 100
    assert tidy_diag("  Два\n\nпредложения. \x1b[0m ") == "Два предложения."
    assert len(tidy_diag("я" * 1000)) <= 450
