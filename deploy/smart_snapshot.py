#!/usr/bin/env python3
"""Снимок SMART всех дисков в компактный JSON для HomeBot.

Запускается от root (homebot-smart.timer), использует только стандартную библиотеку.
Пишет атомарно: временный файл + rename, права 0644 — бот читает, но не может изменить.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Any

DEFAULT_OUT = "/var/lib/homebot/smart.json"
SELFTEST_STAMP = "/var/lib/homebot/smart-selftest.stamp"
SELFTEST_EVERY = 7 * 86400  # короткий самотест раз в неделю, ночью
SELFTEST_HOURS = range(2, 6)


def smartctl(*args: str) -> dict[str, Any]:
    # Код возврата smartctl — битовая маска предупреждений, поэтому смотрим только на JSON.
    proc = subprocess.run(["smartctl", "-j", *args], capture_output=True, text=True, timeout=90, check=False)
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def build_disk(device: str, data: dict[str, Any]) -> dict[str, Any]:
    """Из полного вывода `smartctl -j -a` оставляет только то, что показывает бот."""
    nvme = data.get("nvme_smart_health_information_log") or {}
    table = (data.get("ata_smart_attributes") or {}).get("table") or []
    attrs = {a.get("id"): a for a in table if isinstance(a, dict)}

    def raw(attr_id: int) -> int | None:
        return _int(((attrs.get(attr_id) or {}).get("raw") or {}).get("value"))

    def worst(*values: int | None) -> int | None:
        known = [v for v in values if v is not None]
        return max(known) if known else None

    selftests = ((data.get("ata_smart_self_test_log") or {}).get("standard") or {}).get("table") or []
    last_test = selftests[0] if selftests and isinstance(selftests[0], dict) else {}
    test_passed = (last_test.get("status") or {}).get("passed")

    status = data.get("smart_status") or {}
    passed = status.get("passed")
    temperature = (data.get("temperature") or {}).get("current", nvme.get("temperature"))
    hours = (data.get("power_on_time") or {}).get("hours", nvme.get("power_on_hours"))
    return {
        "device": device,
        "model": data.get("model_name") or data.get("model_family") or "",
        "passed": passed if isinstance(passed, bool) else None,
        "temperature": _int(temperature),
        "power_on_hours": _int(hours),
        "percentage_used": _int(nvme.get("percentage_used")),
        "reallocated": raw(5),
        "pending": raw(197),
        "size_bytes": _int((data.get("user_capacity") or {}).get("bytes")),
        "uncorrectable": worst(raw(187), raw(198)),
        "media_errors": _int(nvme.get("media_errors")),
        "critical_warning": _int(nvme.get("critical_warning")),
        "spare_left": _int(nvme.get("available_spare")),
        "spare_threshold": _int(nvme.get("available_spare_threshold")),
        "selftest_ok": test_passed if isinstance(test_passed, bool) else None,
    }


def device_args(entry: dict[str, Any], *extra: str) -> list[str]:
    name = entry["name"]
    return [*extra, "-d", entry["type"], name] if isinstance(entry.get("type"), str) else [*extra, name]


def selftest_due(stamp_path: str, now: float, hour: int) -> bool:
    """Пора ли запустить недельный короткий самотест: прошла неделя и сейчас глубокая ночь."""
    if hour not in SELFTEST_HOURS:
        return False
    try:
        with open(stamp_path, encoding="utf-8") as handle:
            last = float(handle.read().strip() or 0)
    except (OSError, ValueError):
        last = 0.0
    return now - last >= SELFTEST_EVERY


def start_selftests(stamp_path: str) -> None:
    """Запускает короткий самотест на всех дисках (он идёт в фоне; результат попадёт в следующий снимок)."""
    try:
        scan = smartctl("--scan")
    except FileNotFoundError:
        return
    for entry in scan.get("devices") or []:
        if isinstance(entry.get("name"), str):
            try:
                smartctl(*device_args(entry, "-t", "short"))
            except subprocess.TimeoutExpired:
                continue
    try:
        with open(stamp_path, "w", encoding="utf-8") as handle:
            handle.write(str(time.time()))
    except OSError:
        pass


def collect() -> dict[str, Any]:
    try:
        scan = smartctl("--scan")
    except FileNotFoundError:
        return {"updated": int(time.time()), "disks": [], "error": "smartctl не установлен"}
    disks = []
    for entry in scan.get("devices") or []:
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        args = device_args(entry, "-a")
        try:
            data = smartctl(*args)
        except subprocess.TimeoutExpired:
            disks.append({"device": name, "passed": None})
            continue
        if data:
            disks.append(build_disk(name, data))
    return {"updated": int(time.time()), "disks": disks}


def write_atomic(path: str, payload: dict[str, Any]) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".smart-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    if selftest_due(SELFTEST_STAMP, time.time(), time.localtime().tm_hour):
        start_selftests(SELFTEST_STAMP)
    write_atomic(out, collect())
    return 0


if __name__ == "__main__":
    sys.exit(main())
