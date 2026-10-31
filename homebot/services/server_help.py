"""🩺 Помощник о сервере: вопрос обычными словами («почему тормозит», «что съело память») → ответ локальной модели
по свежим фактам с сервера. Только чтение: модель видит цифры, но ничего не выполняет; если нужно действие, она
советует кнопку или команду, а решает человек.
"""

from __future__ import annotations

import asyncio
from typing import Any

SYSTEM = (
    "Ты помощник домашнего сервера. Отвечай по-русски, коротко (до 6 предложений), только по фактам ниже. "
    "Если фактов не хватает, так и скажи. Ничего не выдумывай. Если нужно действие, предложи конкретную команду "
    "бота (/status, /top, /docker, /logs, /cleanup, /repair) или шаг, но не выполняй его сам.\n\nФакты о сервере:\n"
)


def _gb(n: float) -> str:
    return f"{n / 1024**3:.1f} ГБ"


async def facts(system: Any, units: Any, apps: Any, tasks: Any = None) -> str:
    snap, top, unit_list = await asyncio.gather(system.snapshot(), system.top_processes(8), units.status_all(), return_exceptions=True)
    lines: list[str] = []
    if not isinstance(snap, BaseException):
        lines.append(f"CPU {snap.cpu_percent:.0f}% ({snap.cpu_threads} потоков), нагрузка {snap.load_avg}")
        ram = f"{_gb(snap.ram_used)} из {_gb(snap.ram_total)} ({snap.ram_percent:.0f}%)"
        lines.append(f"Память: занято {ram}, swap {snap.swap_percent:.0f}%")
        for d in snap.disks:
            lines.append(f"Диск {d.mountpoint}: {d.percent:.0f}% занято")
        if snap.temperature is not None:
            lines.append(f"Температура {snap.temperature.current:.0f}°C")
        lines.append(f"Работает {snap.uptime_sec / 3600:.0f} ч")
    if not isinstance(top, BaseException) and top:
        lines.append("Больше всего памяти: " + ", ".join(f"{p.name} {_gb(p.rss)}" for p in top))
    if not isinstance(unit_list, BaseException):
        failed = [u.name for u in unit_list if getattr(u, "active_state", "") == "failed"]
        lines.append("Упавшие службы: " + (", ".join(failed) if failed else "нет"))
    if getattr(apps, "docker", None) is not None:
        try:
            raw = await asyncio.wait_for(apps.docker.containers_raw(), 8)
            bad = [str((c.get("Names") or ["?"])[0]).lstrip("/") for c in raw if isinstance(c, dict) and c.get("State") != "running"]
            lines.append(f"Контейнеров: {len(raw)}, не работают: " + (", ".join(bad) if bad else "нет"))
        except Exception:  # Docker не ответил — без этого пункта
            pass
    if tasks is not None:
        broken = [t for t, _ in tasks.broken()]
        if broken:
            lines.append("Фоновые задачи бота, остановленные после падений: " + ", ".join(broken))
    return "\n".join(f"- {line}" for line in lines)


async def answer(ollama: Any, question: str, fact_text: str) -> str:
    return await ollama.ask(SYSTEM + fact_text, question[:1000])
