import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from aiogram.exceptions import TelegramAPIError

from homebot.jobs.alerts import AlertsJob
from homebot.services.system import DiskInfo, SystemSnapshot, TempReading
from homebot.services.units import UnitStatus


def make_snapshot(cpu=10.0, ram=20.0, disk=30.0, temp=50.0) -> SystemSnapshot:
    return SystemSnapshot(
        taken_at=datetime(2026, 10, 13, tzinfo=UTC), cpu_percent=cpu, cpu_threads=8, load_avg=None,
        ram_total=100, ram_used=int(ram), ram_percent=ram, swap_total=0, swap_used=0, swap_percent=0.0,
        disks=(DiskInfo("/", 100, int(disk), disk),), temperature=TempReading("coretemp", "pkg", temp, 100.0),
        uptime_sec=1.0,
    )  # fmt: skip


class Env:
    """Подменяемые «показания» + журнал отправленных сообщений."""

    def __init__(self, *, fail_delivery: bool = False, markup=None, llm_busy=None) -> None:
        self.snap = make_snapshot()
        self.statuses: list[UnitStatus] = []
        self.sent: list[str] = []
        self.markups: list[object] = []
        self.fail_delivery = fail_delivery

        env = self

        class Bot:
            async def send_message(self, chat_id, text, reply_markup=None):
                if env.fail_delivery:
                    raise TelegramAPIError(method=None, message="network down")  # type: ignore[arg-type]
                assert chat_id == 42
                env.sent.append(text)
                env.markups.append(reply_markup)

        class System:
            async def snapshot(self):
                return env.snap

        class Units:
            async def status_all(self):
                return env.statuses

        settings = SimpleNamespace(
            alert_interval_sec=60, alert_cooldown_sec=1800, alert_cpu_percent=90.0, alert_cpu_consecutive=3,
            alert_ram_percent=90.0, alert_disk_percent=90.0, alert_temp_c=85.0,
        )  # fmt: skip
        self.job = AlertsJob(
            bot=Bot(), admin_id=42, settings=settings, system=System(), units=Units(), markup=markup, llm_busy=llm_busy
        )  # type: ignore[arg-type]

    def tick(self) -> None:
        asyncio.run(self.job._tick())


def test_quiet_when_everything_is_normal():
    env = Env()
    for _ in range(5):
        env.tick()
    assert env.sent == []


def test_cpu_needs_consecutive_breaches():
    env = Env()
    env.snap = make_snapshot(cpu=99)
    env.tick()
    env.tick()
    assert env.sent == []  # два пика подряд — ещё не тревога
    env.snap = make_snapshot(cpu=10)
    env.tick()  # серия прервалась
    env.snap = make_snapshot(cpu=99)
    env.tick()
    env.tick()
    assert env.sent == []
    env.tick()  # третий подряд
    assert len(env.sent) == 1 and "CPU" in env.sent[0]


def test_cooldown_then_reset_after_recovery():
    env = Env()
    env.snap = make_snapshot(ram=95)
    env.tick()
    env.tick()
    env.tick()
    assert len(env.sent) == 1  # повторы подавлены кулдауном

    env.snap = make_snapshot(ram=40)
    env.tick()  # проблема ушла
    env.snap = make_snapshot(ram=96)
    env.tick()
    assert len(env.sent) == 2  # новая вспышка уведомляет сразу, не дожидаясь кулдауна


def test_multiple_problems_in_one_message_and_html_escaped():
    env = Env()
    env.snap = make_snapshot(ram=95, disk=97, temp=90)
    env.statuses = [
        UnitStatus("we<ird>&unit", "loaded", "failed", "failed"),
        UnitStatus("ssh", "loaded", "active", "running"),
    ]
    env.tick()
    assert len(env.sent) == 1
    message = env.sent[0]
    for expected in ("RAM", "Диск", "Температура", "упала"):
        assert expected in message
    assert "we&lt;ird&gt;&amp;unit" in message and "ssh" not in message


def test_failed_delivery_is_retried_next_tick():
    env = Env(fail_delivery=True)
    env.snap = make_snapshot(ram=95)
    env.tick()
    assert env.sent == []  # отправка не удалась, но исключение наружу не вышло
    env.fail_delivery = False
    env.tick()
    assert len(env.sent) == 1  # повод не «сгорел» и доставлен при следующей проверке


def test_inactive_service_is_not_an_alert_only_failed_is():
    env = Env()
    env.statuses = [UnitStatus("ssh", "loaded", "inactive", "dead"), UnitStatus("x", "not-found", "inactive", "dead")]
    env.tick()
    assert env.sent == []


def test_failed_service_alert_gets_buttons_and_other_alerts_do_not():
    seen = []

    def markup(failed):
        seen.append(list(failed))
        return "KEYBOARD"

    env = Env(markup=markup)
    env.statuses = [UnitStatus("ollama", "loaded", "failed", "failed"), UnitStatus("ssh", "loaded", "active", "running")]
    env.tick()
    assert seen == [["ollama"]] and env.markups == ["KEYBOARD"]

    other = Env(markup=markup)
    other.snap = make_snapshot(ram=95)  # не служба — кнопок нет
    other.tick()
    assert other.markups == [None] and seen == [["ollama"]]


def test_high_cpu_while_the_local_model_works_is_not_an_alert():
    import homebot.jobs.alerts as alerts_module

    busy = [True]
    env = Env(llm_busy=lambda: busy[0])
    env.snap = make_snapshot(cpu=99)
    for _ in range(6):
        env.tick()
    assert env.sent == []  # модель считает — загрузка ожидаема

    busy[0] = False
    env.tick()
    env.tick()
    env.tick()
    assert env.sent == []  # хвост после модели (grace) тоже не считаем

    env.job._llm_until = alerts_module.time.monotonic() - 1  # grace закончился, а CPU всё ещё 99%
    env.tick()
    env.tick()
    assert env.sent == []
    env.tick()
    assert len(env.sent) == 1 and "CPU" in env.sent[0]  # настоящая проблема без модели по-прежнему сообщается
