"""SSH-события из журнала systemd: разбор строк sshd, агрегация неудачных попыток, история входов."""

from __future__ import annotations

import ipaddress
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, tzinfo

from homebot.utils.proc import CommandResult, run_command

# OpenSSH >= 9.8 пишет в журнал от имени sshd-session, более старый — sshd.
JOURNAL_BASE = ("journalctl", "--no-pager", "-t", "sshd", "-t", "sshd-session")

_IP = r"[0-9a-fA-F:.]+"
_LOGIN_RE = re.compile(rf"Accepted (?P<method>[\w-]+) for (?P<user>\S+) from (?P<ip>{_IP}) port \d+")
# «Failed password for invalid user x» не считаем здесь: такая попытка уже учтена строкой «Invalid user x».
_FAIL_RE = re.compile(rf"Failed (?P<method>[\w-]+) for (?!invalid user )(?P<user>\S+) from (?P<ip>{_IP}) port \d+")
_INVALID_RE = re.compile(rf"Invalid user (?P<user>\S*) from (?P<ip>{_IP})")
_JOURNAL_LINE_RE = re.compile(r"^(?P<ts>\S+)\s+\S+\s+[^\s:]+:\s+(?P<msg>.*)$")

_TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
_TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


@dataclass(frozen=True, slots=True)
class SshEvent:
    kind: str  # "login" | "fail"
    user: str
    ip: str
    method: str = ""


@dataclass(frozen=True, slots=True)
class LoginRecord:
    when: datetime | None
    user: str
    ip: str
    method: str


def parse_ssh_line(message: str) -> SshEvent | None:
    """Разбирает текст сообщения sshd; всё, что не вход и не неудачная попытка, даёт None."""
    if match := _LOGIN_RE.search(message):
        return SshEvent("login", match["user"], match["ip"], match["method"])
    if match := _FAIL_RE.search(message):
        return SshEvent("fail", match["user"], match["ip"], match["method"])
    if match := _INVALID_RE.search(message):
        return SshEvent("fail", match["user"] or "?", match["ip"], "invalid user")
    return None


def origin_of(ip: str) -> str:
    """'tailscale' | 'lan' | 'local' | 'internet' — откуда пришло подключение."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "internet"
    if addr.is_loopback:
        return "local"
    if addr in _TAILSCALE_V4 or addr in _TAILSCALE_V6:
        return "tailscale"
    if addr.is_private or addr.is_link_local:
        return "lan"
    return "internet"


def parse_journal_time(token: str, tz: tzinfo | None) -> datetime | None:
    """Время из `journalctl -o short-iso`: 2026-10-13T10:02:11+0300."""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            parsed = datetime.strptime(token, fmt)
        except ValueError:
            continue
        return parsed.astimezone(tz) if tz is not None else parsed
    return None


@dataclass(slots=True)
class FailBuffer:
    """Копит неудачные попытки и выдаёт одну сводку вместо десятков сообщений."""

    _by_ip: Counter[str] = field(default_factory=Counter)
    _by_user: Counter[str] = field(default_factory=Counter)

    def add(self, event: SshEvent) -> None:
        self._by_ip[event.ip] += 1
        self._by_user[event.user] += 1

    @property
    def total(self) -> int:
        return sum(self._by_ip.values())

    def take(self) -> tuple[int, list[tuple[str, int]], list[tuple[str, int]]] | None:
        """(всего, топ IP, топ логинов) и очистка; None, если попыток не было."""
        if not self._by_ip:
            return None
        result = (self.total, self._by_ip.most_common(3), self._by_user.most_common(3))
        self._by_ip.clear()
        self._by_user.clear()
        return result


class SshLog:
    """Чтение истории из journald (нужна группа systemd-journal — см. unit-файл)."""

    def __init__(self, tz: tzinfo | None = None) -> None:
        self._tz = tz

    async def recent_logins(self, limit: int = 5, days: int = 7) -> tuple[list[LoginRecord], str | None]:
        """Последние успешные входы (новые первыми) и текст ошибки, если журнал недоступен."""
        result = await run_command(
            *JOURNAL_BASE, "--since", f"-{days}d", "--grep", "Accepted", "-o", "short-iso", "-n", "300", timeout=15
        )
        if error := _journal_error(result):
            return [], error
        records: list[LoginRecord] = []
        for line in result.stdout.splitlines():
            parsed = _JOURNAL_LINE_RE.match(line)
            if not parsed:
                continue
            event = parse_ssh_line(parsed["msg"])
            if event is not None and event.kind == "login":
                records.append(LoginRecord(parse_journal_time(parsed["ts"], self._tz), event.user, event.ip, event.method))
        records.reverse()
        return records[:limit], None

    async def failed_count(self, hours: int = 24) -> tuple[int, str | None]:
        result = await run_command(
            *JOURNAL_BASE,
            "--since",
            f"-{hours}h",
            "--grep",
            "Failed password|Failed publickey|Invalid user",
            "-o",
            "cat",
            timeout=15,
        )
        if error := _journal_error(result):
            return 0, error
        count = sum(1 for line in result.stdout.splitlines() if (e := parse_ssh_line(line)) and e.kind == "fail")
        return count, None


def _journal_error(result: CommandResult) -> str | None:
    if not result.ok:
        return result.problem
    if "insufficient permissions" in result.stderr.lower():
        return "нет доступа к журналу (нужна группа systemd-journal)"
    return None
