"""Состояние дисков (SMART).

Читать SMART может только root, а у бота прав нет и быть не должно. Поэтому раз в час отдельный
root-таймер (`deploy/smart_snapshot.py`) пишет сводку в JSON, а бот только читает этот файл.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STALE_AFTER_SEC = 4 * 3600


@dataclass(frozen=True, slots=True)
class SmartDisk:
    device: str
    model: str = ""
    passed: bool | None = None
    temperature: float | None = None
    power_on_hours: int | None = None
    percentage_used: int | None = None  # износ NVMe, %
    reallocated: int | None = None
    pending: int | None = None
    size_bytes: int | None = None
    uncorrectable: int | None = None  # неисправимые ошибки чтения (ATA 187/198)
    media_errors: int | None = None  # NVMe
    critical_warning: int | None = None  # NVMe, битовая маска; 0 — всё хорошо
    spare_left: int | None = None  # NVMe: оставшийся запас ячеек, %
    spare_threshold: int | None = None
    selftest_ok: bool | None = None  # итог последнего самотеста; None — не запускался


@dataclass(frozen=True, slots=True)
class SmartReport:
    updated: int
    disks: tuple[SmartDisk, ...]
    error: str | None = None

    def age_sec(self, now: float | None = None) -> float:
        return (time.time() if now is None else now) - self.updated

    def is_stale(self, now: float | None = None) -> bool:
        return self.age_sec(now) > STALE_AFTER_SEC


def _opt_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _opt_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def parse_smart_data(data: Any) -> SmartReport:
    """Разбирает JSON, который пишет deploy/smart_snapshot.py. Терпим к лишним и пропущенным полям."""
    if not isinstance(data, dict):
        raise ValueError("ожидался JSON-объект")
    disks: list[SmartDisk] = []
    for item in data.get("disks", []):
        if not isinstance(item, dict) or not isinstance(item.get("device"), str):
            continue
        passed = item.get("passed")
        disks.append(
            SmartDisk(
                device=item["device"],
                model=str(item.get("model") or ""),
                passed=passed if isinstance(passed, bool) else None,
                temperature=_opt_float(item.get("temperature")),
                power_on_hours=_opt_int(item.get("power_on_hours")),
                percentage_used=_opt_int(item.get("percentage_used")),
                reallocated=_opt_int(item.get("reallocated")),
                pending=_opt_int(item.get("pending")),
                size_bytes=_opt_int(item.get("size_bytes")),
                uncorrectable=_opt_int(item.get("uncorrectable")),
                media_errors=_opt_int(item.get("media_errors")),
                critical_warning=_opt_int(item.get("critical_warning")),
                spare_left=_opt_int(item.get("spare_left")),
                spare_threshold=_opt_int(item.get("spare_threshold")),
                selftest_ok=item.get("selftest_ok") if isinstance(item.get("selftest_ok"), bool) else None,
            )
        )
    error = data.get("error")
    return SmartReport(updated=_opt_int(data.get("updated")) or 0, disks=tuple(disks), error=str(error) if error else None)


def smart_issues(report: SmartReport, *, temp_limit: float = 70.0, wear_limit: int = 90) -> list[str]:
    """Человекочитаемые проблемы; пустой список — всё в порядке."""
    issues: list[str] = []
    for disk in report.disks:
        name = disk.device
        if disk.passed is False:
            issues.append(f"{name}: SMART сообщает о неисправности диска")
        if disk.reallocated:
            issues.append(f"{name}: переназначенных секторов — {disk.reallocated}")
        if disk.pending:
            issues.append(f"{name}: нестабильных секторов — {disk.pending}")
        if disk.uncorrectable:
            issues.append(f"{name}: неисправимых ошибок чтения — {disk.uncorrectable}")
        if disk.media_errors:
            issues.append(f"{name}: ошибок носителя NVMe — {disk.media_errors}")
        if disk.critical_warning:
            issues.append(f"{name}: NVMe сообщает о проблеме (код {disk.critical_warning})")
        if disk.spare_left is not None and disk.spare_threshold is not None and disk.spare_left <= disk.spare_threshold:
            issues.append(f"{name}: запас ячеек NVMe на исходе ({disk.spare_left}%)")
        if disk.selftest_ok is False:
            issues.append(f"{name}: последний самотест SMART завершился с ошибкой")
        if disk.percentage_used is not None and disk.percentage_used >= wear_limit:
            issues.append(f"{name}: износ SSD {disk.percentage_used}%")
        if disk.temperature is not None and disk.temperature >= temp_limit:
            issues.append(f"{name}: температура {disk.temperature:.0f} °C")
    return issues


class SmartService:
    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self) -> SmartReport | None:
        """None — файла нет (SMART-таймер не настроен); исключение не бросает."""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            log.warning("Cannot read SMART snapshot %s: %s", self._path, exc)
            return SmartReport(0, (), error=f"не удалось прочитать: {exc.strerror or exc}")
        try:
            return parse_smart_data(json.loads(raw))
        except ValueError as exc:
            log.warning("SMART snapshot %s is invalid: %s", self._path, exc)
            return SmartReport(0, (), error="файл SMART повреждён")
