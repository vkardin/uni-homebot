"""Журнал ядра: ранние признаки проблем с диском, памятью и охлаждением.

SMART отвечает на вопрос «диск уже болен?», журнал ядра — «диск прямо сейчас сыплет ошибками?».
Ошибки ввода-вывода, сбои SATA-кабеля, «файловая система стала только для чтения» и убийца OOM видны
здесь раньше всего. Читаем через journald (нужна группа systemd-journal — как для SSH-журнала).
"""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass

from homebot.utils.proc import run_command


@dataclass(frozen=True, slots=True)
class Rule:
    key: str
    title: str
    advice: str
    pattern: re.Pattern[str]


def _rule(key: str, title: str, advice: str, pattern: str) -> Rule:
    return Rule(key, title, advice, re.compile(pattern, re.IGNORECASE))


RULES: tuple[Rule, ...] = (
    _rule(
        "io",
        "💽 Ошибки ввода-вывода диска",
        "Диск или кабель сыплют ошибками: загляните в SMART (🛡 Безопасность) и сделайте копию важного.",
        r"buffer i/o error|blk_update_request: (?:i/o|critical (?:medium|target|space)) error|print_req_error",
    ),
    _rule(
        "fs",
        "🗂 Проблемы файловой системы",
        "Файловая система нашла повреждения или перешла в режим «только чтение» — нужна проверка (fsck) после остановки сервисов.",
        r"ext4-fs (?:error|warning)|xfs \(.*\):.*(?:corruption|metadata|i/o error)|btrfs: ?(?:error|critical)"
        r"|btrfs (?:error|critical)|remounting filesystem read-only|remounted .* read-only|fat-fs.*error",
    ),
    _rule(
        "ata",
        "🔌 Сбои SATA-диска или кабеля",
        "Частые сбросы канала обычно значат плохой кабель/разъём или умирающий диск.",
        r"\bata\d+(?:\.\d+)?: (?:failed command|exception|comreset failed|hard resetting link|serror|status: \{ drdy err)"
        r"|\bsd \d+:\d+:\d+:\d+:.*(?:failed result|medium error|unrecovered read error)",
    ),
    _rule(
        "nvme",
        "⚡ Сбои NVMe-накопителя",
        "NVMe отвечает с таймаутами или сбрасывается: проверьте температуру, прошивку и SMART.",
        r"nvme\d*(?:n\d+)?: .*(?:timeout|controller is down|abort|removing after probe failure|reset controller)",
    ),
    _rule(
        "oom",
        "💥 Нехватка памяти (OOM)",
        "Ядро убило процесс из-за нехватки RAM. Загляните в топ процессов и в Docker — что съело память.",
        r"out of memory: killed process|oom-kill:|invoked oom-killer",
    ),
    _rule(
        "thermal",
        "🌡 Перегрев процессора",
        "Процессор сбрасывает частоту из-за жары: проверьте вентилятор, пыль и вентиляцию.",
        r"temperature above threshold|cpu clock throttled|thermal throttling|package temperature above",
    ),
    _rule(
        "hw",
        "🧨 Аппаратные ошибки (память/шина)",
        "Ядро зафиксировало аппаратную ошибку. Если повторяется — проверьте модули памяти и питание.",
        r"mce: .*hardware error|machine check (?:events logged|exception)|edac .*(?:ue|uncorrected)|aer:.*uncorrected",
    ),
)


@dataclass(frozen=True, slots=True)
class KernelProblem:
    rule: Rule
    count: int
    sample: str

    @property
    def key(self) -> str:
        return self.rule.key


def classify(lines: Iterable[str]) -> list[KernelProblem]:
    """Группирует строки журнала по правилам. Одна строка относится к первому подошедшему правилу."""
    counts: OrderedDict[str, int] = OrderedDict()
    samples: dict[str, str] = {}
    by_key = {rule.key: rule for rule in RULES}
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        for rule in RULES:
            if rule.pattern.search(line):
                counts[rule.key] = counts.get(rule.key, 0) + 1
                samples.setdefault(rule.key, line[:200])
                break
    return [KernelProblem(by_key[key], count, samples[key]) for key, count in counts.items()]


async def read_kernel_log(since_ts: int) -> tuple[list[str], str | None]:
    """Строки журнала ядра (предупреждения и хуже) с момента `since_ts`; вторым значением — причина сбоя чтения."""
    result = await run_command("journalctl", "-k", "--no-pager", "-o", "cat", "-p", "warning", "--since", f"@{since_ts}", timeout=20)
    if not result.ok:
        return [], result.problem
    if "insufficient permissions" in result.stderr.lower() or "not seeing messages" in result.stderr.lower():
        return [], "нет доступа к журналу (нужна группа systemd-journal)"
    return result.stdout.splitlines(), None
