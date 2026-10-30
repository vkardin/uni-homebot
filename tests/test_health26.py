"""2.6: журнал ядра и расширенный SMART."""

import asyncio
import importlib.util
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from aiogram.exceptions import TelegramAPIError

from homebot.jobs.kernel_watch import COOLDOWN, KernelWatchJob
from homebot.services.kernel_log import classify
from homebot.services.smart import parse_smart_data, smart_issues
from homebot.services.state import StateStore
from homebot.ui.extra_texts import plural, render_kernel_problems

ROOT = Path(__file__).resolve().parent.parent

IO_LINES = [
    "blk_update_request: I/O error, dev sda, sector 123456 op 0x0:(READ) flags 0x80700 phys_seg 1 prio class 2",
    "Buffer I/O error on dev sda1, logical block 15432, async page read",
    "EXT4-fs error (device sda1): ext4_find_entry:1455: inode #2: comm ls: reading directory lblock 0",
    "ata1.00: exception Emask 0x0 SAct 0x1000 SErr 0x0 action 0x6 frozen",
    "ata1: hard resetting link",
    "Out of memory: Killed process 4242 (java) total-vm:8123456kB, anon-rss:7000000kB",
    "CPU0: Core temperature above threshold, cpu clock throttled (total events = 12)",
    "nvme nvme0: I/O 77 QID 3 timeout, aborting",
    "mce: [Hardware Error]: Machine check events logged",
    "usb 1-1: new high-speed USB device number 4 using xhci_hcd",  # шум
    "",
]


def test_classify_groups_and_ignores_noise():
    problems = {p.key: p for p in classify(IO_LINES)}
    assert set(problems) == {"io", "fs", "ata", "oom", "thermal", "nvme", "hw"}
    assert problems["io"].count == 2 and problems["ata"].count == 2
    assert "sector 123456" in problems["io"].sample and problems["oom"].sample.startswith("Out of memory")
    assert classify(["usb 1-1: new device", "", "wlan0: associated"]) == []
    assert classify(["Remounting filesystem read-only"])[0].key == "fs"
    assert classify(["XFS (sda1): Metadata corruption detected at xfs_inode_buf_verify"])[0].key == "fs"
    assert classify(["sd 0:0:0:0: [sda] tag#5 FAILED Result: hostbyte=DID_ERROR"])[0].key == "ata"


def test_render_kernel_problems_escapes_and_pluralizes():
    text = render_kernel_problems(classify(["Buffer I/O error on dev <sda1>"] * 5 + ["Out of memory: Killed process 1 (a&b)"]))
    assert "5 записей" in text and "1 запись" in text and "&lt;sda1&gt;" in text and "a&amp;b" in text
    assert [plural(n, "запись", "записи", "записей") for n in (1, 2, 5, 11, 21, 22, 112)] == [
        "запись", "записи", "записей", "записей", "запись", "записи", "записей",
    ]  # fmt: skip


class FakeBot:
    def __init__(self):
        self.sent: list[str] = []
        self.fail = False

    async def send_message(self, chat_id, text):
        if self.fail:
            raise TelegramAPIError(method=None, message="offline")  # type: ignore[arg-type]
        self.sent.append(text)
        return SimpleNamespace(message_id=1)


def test_kernel_watch_alerts_once_per_cooldown_and_keeps_window_on_failure():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "s.json")
            bot = FakeBot()
            queue: list[list[str]] = []
            windows: list[int] = []

            async def read(since):
                windows.append(since)
                return (queue.pop(0) if queue else []), None

            job = KernelWatchJob(bot=bot, admin_id=1, store=store, read=read)  # type: ignore[arg-type]
            assert await job.tick(1000) == 0  # тихо
            assert windows == [1000 - 600]  # первый запуск: короткое окно, а не вся история

            queue.append(["Buffer I/O error on dev sda"])
            assert await job.tick(1300) == 1 and "ввода-вывода" in bot.sent[0]
            assert windows[-1] == 1000  # окно — с прошлой проверки

            queue.append(["Buffer I/O error on dev sda"])
            assert await job.tick(1600) == 0  # та же категория — в кулдауне
            queue.append(["Buffer I/O error on dev sda", "Out of memory: Killed process 1 (x)"])
            assert await job.tick(1900) == 1 and "OOM" in bot.sent[1] and "ввода-вывода" not in bot.sent[1]

            queue.append(["Buffer I/O error on dev sda"])
            assert await job.tick(1900 + COOLDOWN) == 1  # кулдаун прошёл

            bot.fail = True
            queue.append(["CPU0: Core temperature above threshold"])
            before = store.get("kernel.last_check")
            assert await job.tick(2000 + COOLDOWN) == 0
            assert store.get("kernel.last_check") == before  # не доставили — окно не сдвинули
            bot.fail = False
            queue.append(["CPU0: Core temperature above threshold"])
            assert await job.tick(2100 + COOLDOWN) == 1

    asyncio.run(scenario())


def test_kernel_watch_without_journal_access_is_silent_but_alive():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            bot = FakeBot()

            async def read(since):
                return [], "нет доступа к журналу (нужна группа systemd-journal)"

            job = KernelWatchJob(bot=bot, admin_id=1, store=StateStore(Path(tmp) / "s.json"), read=read)  # type: ignore[arg-type]
            assert await job.tick(1000) == 0 and await job.tick(1300) == 0
            assert bot.sent == []

    asyncio.run(scenario())


# ------------------------------------------------------------------ SMART 2.6
def _snapshot_script():
    spec = importlib.util.spec_from_file_location("smart_snapshot", ROOT / "deploy" / "smart_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NVME_BAD = {
    "model_name": "Some NVMe",
    "smart_status": {"passed": True},
    "nvme_smart_health_information_log": {
        "critical_warning": 4, "media_errors": 3, "available_spare": 4, "available_spare_threshold": 10,
        "percentage_used": 5, "temperature": 40,
    },
}  # fmt: skip
ATA_TESTFAIL = {
    "model_family": "WD",
    "smart_status": {"passed": True},
    "ata_smart_attributes": {"table": [{"id": 187, "raw": {"value": 2}}, {"id": 198, "raw": {"value": 7}}]},
    "ata_smart_self_test_log": {"standard": {"table": [{"status": {"passed": False, "string": "Completed: read failure"}}]}},
}


def test_snapshot_extracts_new_attributes_and_bot_reports_them():
    script = _snapshot_script()
    nvme, ata = script.build_disk("/dev/nvme0", NVME_BAD), script.build_disk("/dev/sda", ATA_TESTFAIL)
    assert nvme["media_errors"] == 3 and nvme["critical_warning"] == 4 and nvme["spare_left"] == 4 and nvme["selftest_ok"] is None
    assert ata["uncorrectable"] == 7 and ata["selftest_ok"] is False  # берём худшее из 187/198
    json.dumps([nvme, ata])

    report = parse_smart_data({"updated": 1, "disks": [nvme, ata]})
    issues = smart_issues(report)
    joined = "\n".join(issues)
    for expected in (
        "ошибок носителя NVMe — 3",
        "код 4",
        "запас ячеек NVMe на исходе (4%)",
        "неисправимых ошибок чтения — 7",
        "самотест",
    ):
        assert expected in joined, expected

    healthy = parse_smart_data(
        {"updated": 1, "disks": [{"device": "/dev/nvme0", "critical_warning": 0, "media_errors": 0, "available_spare": 100,
                                  "available_spare_threshold": 10, "selftest_ok": True, "uncorrectable": 0}]}
    )  # fmt: skip
    assert smart_issues(healthy) == []
    assert (
        parse_smart_data({"disks": [{"device": "/dev/x", "selftest_ok": "yes", "media_errors": "many"}]}).disks[0].selftest_ok is None
    )


def test_weekly_selftest_schedule_and_device_args():
    script = _snapshot_script()
    with tempfile.TemporaryDirectory() as tmp:
        stamp = str(Path(tmp) / "stamp")
        day = 86400
        assert script.selftest_due(stamp, 10 * day, 3)  # штампа нет, ночь
        assert not script.selftest_due(stamp, 10 * day, 14)  # днём не трогаем диски
        Path(stamp).write_text(str(10 * day - 2 * day))
        assert not script.selftest_due(stamp, 10 * day, 3)  # прошло меньше недели
        assert script.selftest_due(stamp, 10 * day + 6 * day, 3)
        Path(stamp).write_text("мусор")
        assert script.selftest_due(stamp, 10 * day, 2)
    assert script.device_args({"name": "/dev/sda"}, "-a") == ["-a", "/dev/sda"]
    assert script.device_args({"name": "/dev/nvme0", "type": "nvme"}, "-t", "short") == ["-t", "short", "-d", "nvme", "/dev/nvme0"]
