"""⚡ Сценарии «если, то»: условие → действие, проверка раз в минуту.

Условия: нагрузка (cpu, ram, disk, temp выше порога), служба упала, контейнер не работает, курс P2P выше или
ниже порога. Действия: сообщение, перезапуск контейнера или службы (только разрешённых: DOCKER_RESTART_ENABLED и
RESTART_SERVICES — те же проверки, что у кнопок). Срабатывает, когда условие становится верным, и не чаще
раза в `cooldown` минут. Хранится в state.json (ключ `rules`).
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any

KEY = "rules"
MAX_RULES = 30
WHEN = {
    "cpu": "Процессор выше, %",
    "ram": "Память выше, %",
    "disk": "Диск выше, %",
    "temp": "Температура выше, °C",
    "service_failed": "Служба упала",
    "container_down": "Контейнер не работает",
    "p2p_above": "Курс P2P выше",
    "p2p_below": "Курс P2P ниже",
}
THEN = {"notify": "Написать мне", "restart_container": "Перезапустить контейнер", "restart_service": "Перезапустить службу"}
NUMERIC = {"cpu", "ram", "disk", "temp", "p2p_above", "p2p_below"}
_NAME_RE = re.compile(r"^[\w.@-]{1,64}$")


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    when: str
    value: float = 0.0  # порог
    target: str = ""  # служба или контейнер для условия
    then: str = "notify"
    then_target: str = ""  # что перезапустить
    cooldown: int = 30  # минут
    on: bool = True


def make(raw: dict[str, Any]) -> Rule:
    when, then = str(raw.get("when", "")), str(raw.get("then", "notify"))
    if when not in WHEN or then not in THEN:
        raise ValueError("неизвестное условие или действие")
    value = 0.0
    if when in NUMERIC:
        try:
            value = float(str(raw.get("value", "")).replace(",", "."))
        except ValueError:
            raise ValueError("нужен порог числом") from None
    target = str(raw.get("target", "")).strip()
    if when in {"service_failed", "container_down"} and not _NAME_RE.match(target):
        raise ValueError("укажите имя службы или контейнера")
    then_target = str(raw.get("then_target", "")).strip() or (target if then != "notify" else "")
    if then != "notify" and not _NAME_RE.match(then_target):
        raise ValueError("укажите, что перезапустить")
    cooldown = max(5, min(1440, int(raw.get("cooldown", 30) or 30)))
    return Rule(
        str(raw.get("id") or uuid.uuid4().hex[:8]), when, value, target, then, then_target, cooldown, bool(raw.get("on", True))
    )


def load(store: Any) -> list[Rule]:
    out = []
    for raw in store.get(KEY) or []:
        try:
            out.append(make(raw))
        except (ValueError, TypeError, AttributeError):
            continue
    return out


async def save(store: Any, items: list[Rule]) -> None:
    await store.set(KEY, [asdict(r) for r in items[:MAX_RULES]])


def describe(r: Rule) -> str:
    cond = WHEN[r.when].replace(", %", "").replace(", °C", "")
    cond += f" {r.value:g}" if r.when in NUMERIC else f": {r.target}"
    act = THEN[r.then] + (f" {r.then_target}" if r.then != "notify" else "")
    return f"Если {cond.lower()} → {act.lower()}"


def check(r: Rule, facts: dict[str, Any]) -> bool | None:
    """Верно ли условие; None — данных нет (не трогаем состояние)."""
    if r.when in {"cpu", "ram", "disk", "temp"}:
        v = facts.get(r.when)
        return None if v is None else v > r.value
    if r.when == "service_failed":
        failed = facts.get("failed")
        return None if failed is None else r.target in failed
    if r.when == "container_down":
        running = facts.get("containers")
        return None if running is None else r.target not in running
    rate = facts.get("p2p")
    if rate is None:
        return None
    return rate > r.value if r.when == "p2p_above" else rate < r.value


class Engine:
    """Помнит, что было верно в прошлый раз, и когда правило срабатывало."""

    def __init__(self) -> None:
        self._was: dict[str, bool] = {}
        self._fired: dict[str, float] = {}

    def due(self, items: list[Rule], facts: dict[str, Any], now: float | None = None) -> list[Rule]:
        now = time.time() if now is None else now
        out = []
        for r in items:
            state = check(r, facts) if r.on else False
            if state is None:
                continue
            was = self._was.get(r.id, False)
            self._was[r.id] = state
            if state and not was and now - self._fired.get(r.id, 0) >= r.cooldown * 60:
                self._fired[r.id] = now
                out.append(r)
        return out
