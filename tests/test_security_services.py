import asyncio
import importlib.util
import json
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from homebot.services.power import PowerService, is_fresh
from homebot.services.smart import SmartService, parse_smart_data, smart_issues
from homebot.services.ssh_log import FailBuffer, origin_of, parse_journal_time, parse_ssh_line
from homebot.services.updates import UpdatesService, parse_upgradable
from homebot.ui.texts import render_security, render_ssh_failures, render_ssh_login

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------------ SSH
def test_parse_ssh_lines():
    login = parse_ssh_line("Accepted publickey for patrik from 100.101.5.7 port 51234 ssh2: ED25519 SHA256:abc")
    assert (login.kind, login.user, login.ip, login.method) == ("login", "patrik", "100.101.5.7", "publickey")
    fail = parse_ssh_line("Failed password for root from 203.0.113.9 port 4444 ssh2")
    assert (fail.kind, fail.user, fail.ip) == ("fail", "root", "203.0.113.9")
    invalid = parse_ssh_line("Invalid user admin from 198.51.100.2 port 5555")
    assert (invalid.kind, invalid.user, invalid.ip) == ("fail", "admin", "198.51.100.2")
    # Повтор той же попытки другой строкой не должен считаться дважды
    assert parse_ssh_line("Failed password for invalid user admin from 198.51.100.2 port 5555 ssh2") is None
    v6 = parse_ssh_line("Accepted password for bob from fd7a:115c:a1e0::1 port 22 ssh2")
    assert v6.ip == "fd7a:115c:a1e0::1"
    for noise in ("Connection closed by 1.2.3.4 port 22 [preauth]", "Server listening on 0.0.0.0 port 22.", ""):
        assert parse_ssh_line(noise) is None


def test_origin():
    assert origin_of("100.101.5.7") == "tailscale"
    assert origin_of("fd7a:115c:a1e0:ab12::1") == "tailscale"
    assert origin_of("192.168.1.10") == "lan"
    assert origin_of("127.0.0.1") == "local"
    assert origin_of("8.8.8.8") == "internet"
    assert origin_of("garbage") == "internet"


def test_fail_buffer_aggregates_and_resets():
    buffer = FailBuffer()
    assert buffer.take() is None
    for ip, user in [("1.1.1.1", "root")] * 5 + [("2.2.2.2", "admin")] * 2:
        buffer.add(parse_ssh_line(f"Invalid user {user} from {ip} port 1"))
    total, ips, users = buffer.take()
    assert total == 7 and ips[0] == ("1.1.1.1", 5) and users[0] == ("root", 5)
    assert buffer.take() is None
    text = render_ssh_failures(total, ips, users, minutes=5)
    assert "7 за 5 мин" in text and "1.1.1.1" in text


def test_journal_time_and_login_render():
    moment = parse_journal_time("2026-10-13T10:02:11+0300", ZoneInfo("Europe/Kyiv"))
    assert moment.hour == 10 and moment.minute == 2
    assert parse_journal_time("garbage", None) is None
    text = render_ssh_login(parse_ssh_line("Accepted publickey for pa<t>rik from 8.8.8.8 port 1 ssh2"))
    assert "pa&lt;t&gt;rik" in text and "интернет" in text


# -------------------------------------------------------------------- updates
APT_OUTPUT = """Listing...
openssl/noble-updates,noble-security 3.0.13-0ubuntu3.5 amd64 [upgradable from: 3.0.13-0ubuntu3.4]
curl/noble-updates 8.5.0-2ubuntu10.6 amd64 [upgradable from: 8.5.0-2ubuntu10.5]
linux-generic/noble-security 6.8.0.50.50 amd64 [upgradable from: 6.8.0.49.49]
"""


def test_parse_upgradable():
    names, security = parse_upgradable(APT_OUTPUT)
    assert names == ["openssl", "curl", "linux-generic"] and security == 2
    assert parse_upgradable("Listing...\n") == ([], 0)


def test_reboot_required_detection():
    with tempfile.TemporaryDirectory() as tmp:
        flag = Path(tmp) / "reboot-required"
        service = UpdatesService(reboot_file=flag)
        info = asyncio.run(service.check())  # apt может отсутствовать — это не должно бросать
        assert info.reboot_required is False
        flag.write_text("*** System restart required ***\n")
        Path(f"{flag}.pkgs").write_text("linux-image-6.8\nlibc6\n")
        info = asyncio.run(service.check())
        assert info.reboot_required is True and info.reboot_packages == ("linux-image-6.8", "libc6")


# ---------------------------------------------------------------------- SMART
def _load_snapshot_script():
    spec = importlib.util.spec_from_file_location("smart_snapshot", ROOT / "deploy" / "smart_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NVME_JSON = {
    "model_name": "Samsung SSD 980 1TB",
    "smart_status": {"passed": True},
    "temperature": {"current": 41},
    "power_on_time": {"hours": 8760},
    "user_capacity": {"bytes": 1000204886016},
    "nvme_smart_health_information_log": {"percentage_used": 3, "temperature": 41, "power_on_hours": 8760},
}
ATA_JSON = {
    "model_family": "Seagate Barracuda",
    "smart_status": {"passed": False},
    "temperature": {"current": 52},
    "power_on_time": {"hours": 30000},
    "ata_smart_attributes": {
        "table": [{"id": 5, "raw": {"value": 8}}, {"id": 197, "raw": {"value": 2}}, {"id": 9, "raw": {"value": 1}}]
    },
}


def test_snapshot_script_transform_and_bot_parsing_agree():
    script = _load_snapshot_script()
    disks = [script.build_disk("/dev/nvme0", NVME_JSON), script.build_disk("/dev/sda", ATA_JSON)]
    assert disks[0]["percentage_used"] == 3 and disks[0]["reallocated"] is None
    assert disks[1]["reallocated"] == 8 and disks[1]["pending"] == 2 and disks[1]["passed"] is False

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sub" / "smart.json"
        script.write_atomic(str(out), {"updated": int(time.time()), "disks": disks})
        assert oct(out.stat().st_mode & 0o777) == "0o644"
        report = SmartService(out).read()

    assert [d.device for d in report.disks] == ["/dev/nvme0", "/dev/sda"]
    assert not report.is_stale()
    issues = smart_issues(report)
    assert any("неисправности" in i for i in issues) and any("переназначенных" in i for i in issues)
    assert all("/dev/nvme0" not in i for i in issues)


def test_smart_service_missing_corrupt_and_thresholds():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "smart.json"
        assert SmartService(path).read() is None
        path.write_text("{oops")
        assert SmartService(path).read().error is not None
    report = parse_smart_data({"updated": 1, "disks": [{"device": "/dev/nvme0", "temperature": 75, "percentage_used": 95}]})
    assert report.is_stale()
    assert len(smart_issues(report)) == 2
    assert parse_smart_data({"disks": ["junk", {"no_device": 1}]}).disks == ()


def test_security_screen_renders_everything():
    from homebot.services.ssh_log import LoginRecord
    from homebot.services.updates import UpdatesInfo

    text = render_security(
        logins=[LoginRecord(parse_journal_time("2026-10-13T10:02:11+0300", None), "patrik", "100.64.0.5", "publickey")],
        logins_error=None,
        failed=12,
        failed_error=None,
        updates=UpdatesInfo(total=3, security=1, reboot_required=True, reboot_packages=("linux",)),
        smart=None,
        tz=ZoneInfo("Europe/Kyiv"),
    )
    for expected in (
        "13.10 10:02",
        "Tailscale",
        "<b>12</b>",
        "security: <b>1</b>",
        "перезагрузка",
        "SMART: не настроен",
    ):
        assert expected in text, expected
    assert len(text) < 4096


# ---------------------------------------------------------------------- power
def test_power_whitelist_and_confirmation_ttl():
    power = PowerService(["ollama", "bad name", "--force", "docker.service"], power_enabled=False)
    assert power.restartable == ("ollama", "docker.service")
    result = asyncio.run(power.restart("ssh"))
    assert not result.ok and "разрешённых" in result.message
    assert not asyncio.run(power.reboot()).ok  # питание выключено конфигом
    now = time.time()
    assert is_fresh(int(now) - 10, now)
    assert not is_fresh(int(now) - 120, now)
    assert not is_fresh(int(now) + 30, now)  # из будущего — подделка/рассинхрон


def test_snapshot_json_roundtrip_is_plain_json():
    script = _load_snapshot_script()
    json.dumps(script.build_disk("/dev/x", {}))  # пустой ответ smartctl не ломает сериализацию
