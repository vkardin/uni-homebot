"""Любые объекты сервисов (dataclass, datetime, Enum, Path) → JSON-совместимые значения."""

from __future__ import annotations

import dataclasses
import enum
from datetime import date, datetime
from pathlib import Path
from typing import Any


def plain(value: Any, _depth: int = 0) -> Any:
    if _depth > 8:
        return None
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name), _depth + 1) for f in dataclasses.fields(value)}
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): plain(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [plain(v, _depth + 1) for v in value]
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)
