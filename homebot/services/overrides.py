"""⚙️ Настройки из панели: часть параметров .env можно поменять без правки файла на сервере.

Значения лежат в `STATE_DIR/settings.json`. `.env` главнее: что задано там явно, панель только показывает
(«задано на сервере») и не меняет. Новые значения применяются при запуске бота, поэтому после сохранения
панель предлагает перезапуск.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import TypeAdapter, ValidationError

if TYPE_CHECKING:
    from homebot.config import Settings

log = logging.getLogger(__name__)
FILE_NAME = "settings.json"


@dataclass(frozen=True, slots=True)
class Option:
    name: str  # поле Settings
    title: str
    group: str
    kind: str  # text, number, hour
    hint: str = ""


OPTIONS: tuple[Option, ...] = (
    Option("language", "Язык", "Общие", "text", "ru, en или uk"),
    Option("timezone", "Часовой пояс", "Общие", "text", "Например Europe/Berlin или America/New_York"),
    Option("currency", "Валюта", "Общие", "text", "Знак для финансов и цен: €, $, ₴, zł"),
    Option("weather_lat", "Широта", "Погода", "number", "Например 50.45"),
    Option("weather_lon", "Долгота", "Погода", "number", "Например 30.52"),
    Option("weather_label", "Название места", "Погода", "text"),
    Option("rates_currencies", "Курсы в сводке", "Погода", "text", "Коды через запятую: USD,EUR. Пусто — не показывать"),
    Option("games_country", "Регион магазинов игр", "Погода", "text", "Две буквы: US, DE, UA"),
    Option("alert_cpu_percent", "Процессор, %", "Алерты", "number"),
    Option("alert_ram_percent", "Память, %", "Алерты", "number"),
    Option("alert_disk_percent", "Диск, %", "Алерты", "number"),
    Option("alert_temp_c", "Температура, °C", "Алерты", "number"),
    Option("backup_hour", "Час бэкапа", "Бэкап", "hour"),
    Option("backup_keep", "Сколько копий хранить", "Бэкап", "number"),
)
BY_NAME: dict[str, Option] = {o.name: o for o in OPTIONS}


def _path(state_dir: Path) -> Path:
    return state_dir / FILE_NAME


def load(state_dir: Path) -> dict[str, Any]:
    """{поле: значение}; битый файл — пусто (и запись в журнал), неизвестные поля отбрасываются."""
    path = _path(state_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("Cannot read %s: %s", path, exc)
        return {}
    return {k: v for k, v in data.items() if k in BY_NAME} if isinstance(data, dict) else {}


def save(state_dir: Path, data: dict[str, Any]) -> None:
    path = _path(state_dir)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def validate(name: str, value: Any) -> Any:
    """Проверенное значение или ValueError с понятным текстом."""
    from homebot.config import Settings

    if name not in BY_NAME:
        raise ValueError("эту настройку нельзя менять из панели")
    field = Settings.model_fields[name]
    if isinstance(value, str):
        value = value.strip()
        if value == "" and field.default is None:
            return None
    try:
        out = TypeAdapter(Annotated[field.annotation, field]).validate_python(value)
    except ValidationError as exc:
        raise ValueError(exc.errors()[0]["msg"]) from None
    if name == "timezone":
        try:
            ZoneInfo(out)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"неизвестный часовой пояс: {out}") from None
    if name == "language":
        out = out.lower()
        if out not in {"ru", "en", "uk"}:
            raise ValueError("ru, en или uk")
    if name == "currency" and len(out) > 5:
        raise ValueError("не длиннее 5 символов")
    if name == "games_country":
        out = out.upper()
        if len(out) != 2 or not out.isalpha():
            raise ValueError("две латинские буквы, например US")
    if name == "backup_hour" and not 0 <= out <= 23:
        raise ValueError("час от 0 до 23")
    return out


def apply(settings: Settings, data: dict[str, Any]) -> Settings:
    """Настройки со значениями из панели — только для того, что не задано в .env явно; ошибочные пропускаются."""
    update: dict[str, Any] = {}
    for name, raw in data.items():
        if name not in BY_NAME or name in settings.model_fields_set:
            continue
        try:
            update[name] = validate(name, raw)
        except ValueError as exc:
            log.warning("settings.json: %s skipped (%s)", name, exc)
    if "currency" in update:
        update |= {n: update["currency"] for n in ("actual_currency", "sites_currency") if n not in settings.model_fields_set}
    if not update:
        return settings
    merged = settings.model_copy()
    for name, value in update.items():  # значение из панели не считается заданным в .env
        object.__setattr__(merged, name, value)
    return merged


def describe(settings: Settings, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Для панели: текущее значение, источник (env, panel, default) и подсказка."""
    out = []
    for o in OPTIONS:
        source = "env" if o.name in settings.model_fields_set else "panel" if o.name in data else "default"
        value = data[o.name] if source == "panel" else getattr(settings, o.name)
        row = {"name": o.name, "title": o.title, "group": o.group, "kind": o.kind, "hint": o.hint}
        out.append(row | {"value": value, "source": source})
    return out
