import asyncio

from homebot.services.units import UnitsService, UnitStatus, friendly_error


def test_invalid_unit_names_are_dropped():
    service = UnitsService(["ssh", "tailscaled", "--help", "a b", "x;rm -rf /", "docker.service", "getty@tty1"])
    assert service.units == ("ssh", "tailscaled", "docker.service", "getty@tty1")


def test_empty_list_returns_empty():
    assert asyncio.run(UnitsService([]).status_all()) == []


def test_status_presentation():
    assert UnitStatus("a", "loaded", "active", "running").emoji == "🟢"
    assert UnitStatus("a", "loaded", "failed", "failed").emoji == "🔴"
    assert UnitStatus("a", "loaded", "failed", "failed").is_failed
    assert UnitStatus("a", "loaded", "inactive", "dead").emoji == "⚪"
    missing = UnitStatus("a", "not-found", "inactive", "dead")
    assert missing.emoji == "❔" and missing.label == "юнит не найден"
    broken = UnitStatus("a", error="таймаут systemctl")
    assert broken.emoji == "❔" and "таймаут" in broken.label
    assert not broken.is_active


def test_systemctl_failure_is_reported_not_raised():
    # В CI/контейнере systemctl может отсутствовать или не подключаться к шине — это не должно ронять бота.
    status = asyncio.run(UnitsService(["definitely-not-a-real-unit-xyz"]).status("definitely-not-a-real-unit-xyz"))
    assert isinstance(status, UnitStatus)
    assert not status.is_active


def test_friendly_errors_are_short_and_russian():
    assert (
        friendly_error("System has not been booted with systemd as init system (PID 1). Can't operate.\n", 1)
        == "systemd недоступен (контейнер?)"
    )
    assert friendly_error("Unit foo.service could not be found.", 4) == "служба не найдена"
    assert friendly_error("", 3) == "код 3"
    long = friendly_error("word " * 40, 1)
    assert len(long) <= 80 and long.endswith("…") and not long.endswith(" …")
