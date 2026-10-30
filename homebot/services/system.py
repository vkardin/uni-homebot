"""Сбор метрик сервера через psutil.

Все вызовы psutil синхронные (и иногда блокирующие: `cpu_percent(interval=...)`),
поэтому выполняются в потоке — event loop бота не блокируется.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, tzinfo

import psutil

log = logging.getLogger(__name__)

_PSEUDO_FS = frozenset(
    {
        "tmpfs", "devtmpfs", "squashfs", "overlay", "proc", "sysfs", "cgroup", "cgroup2",
        "devpts", "securityfs", "debugfs", "tracefs", "fusectl", "configfs", "pstore", "bpf",
        "autofs", "mqueue", "hugetlbfs", "ramfs", "efivarfs", "binfmt_misc", "nsfs", "fuse.lxcfs",
    }
)  # fmt: skip
_SKIPPED_MOUNTS = ("/snap", "/run", "/sys", "/proc", "/dev", "/var/lib/docker", "/var/lib/containers")
# Порядок предпочтения датчиков температуры CPU (Intel / AMD / ARM / ACPI).
_TEMP_SENSORS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal", "acpitz")


@dataclass(frozen=True, slots=True)
class DiskInfo:
    mountpoint: str
    total: int
    used: int
    percent: float


@dataclass(frozen=True, slots=True)
class TempReading:
    sensor: str
    label: str
    current: float
    critical: float | None


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    pid: int
    name: str
    rss: int
    mem_percent: float


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    taken_at: datetime
    cpu_percent: float
    cpu_threads: int
    load_avg: tuple[float, float, float] | None
    ram_total: int
    ram_used: int
    ram_percent: float
    swap_total: int
    swap_used: int
    swap_percent: float
    disks: tuple[DiskInfo, ...]
    temperature: TempReading | None
    uptime_sec: float


def _is_skipped_mount(mountpoint: str) -> bool:
    return any(mountpoint == p or mountpoint.startswith(p + "/") for p in _SKIPPED_MOUNTS)


class SystemService:
    def __init__(self, tz: tzinfo | None = None) -> None:
        self._tz = tz

    async def snapshot(self) -> SystemSnapshot:
        return await asyncio.to_thread(self._snapshot_sync)

    async def top_processes(self, limit: int = 5) -> list[ProcessInfo]:
        return await asyncio.to_thread(self._top_sync, limit)

    # ------------------------------------------------------------------ sync
    def _snapshot_sync(self) -> SystemSnapshot:
        cpu = psutil.cpu_percent(interval=0.5)
        try:
            load: tuple[float, float, float] | None = os.getloadavg()
        except (OSError, AttributeError):
            load = None

        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()

        return SystemSnapshot(
            taken_at=datetime.now(self._tz),
            cpu_percent=cpu,
            cpu_threads=psutil.cpu_count(logical=True) or 0,
            load_avg=load,
            ram_total=vm.total,
            ram_used=vm.total - vm.available,  # «реально занято», без кэша
            ram_percent=vm.percent,
            swap_total=swap.total,
            swap_used=swap.used,
            swap_percent=swap.percent,
            disks=tuple(self._read_disks()),
            temperature=self._read_temperature(),
            uptime_sec=max(0.0, time.time() - psutil.boot_time()),
        )

    @staticmethod
    def _read_disks() -> list[DiskInfo]:
        seen_devices: set[str] = set()
        disks: list[DiskInfo] = []
        for part in psutil.disk_partitions(all=False):
            if part.fstype.lower() in _PSEUDO_FS or _is_skipped_mount(part.mountpoint):
                continue
            if part.device in seen_devices:  # bind-mount'ы и subvolume'ы btrfs
                continue
            seen_devices.add(part.device)
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:  # PermissionError/FileNotFoundError — наследники OSError
                continue
            if usage.total <= 0:
                continue
            disks.append(DiskInfo(part.mountpoint, usage.total, usage.used, usage.percent))
        disks.sort(key=lambda d: d.mountpoint)
        return disks

    @staticmethod
    def _read_temperature() -> TempReading | None:
        reader = getattr(psutil, "sensors_temperatures", None)
        if reader is None:  # не-Linux платформы
            return None
        try:
            sensors = reader()
        except (OSError, NotImplementedError):
            return None
        if not sensors:
            return None

        name = next((n for n in _TEMP_SENSORS if sensors.get(n)), None)
        if name is None:
            name = next((n for n, entries in sensors.items() if entries), None)
        if name is None:
            return None

        hottest = max(sensors[name], key=lambda e: e.current)
        limit = hottest.critical or hottest.high
        return TempReading(
            sensor=name,
            label=hottest.label or name,
            current=float(hottest.current),
            critical=float(limit) if limit else None,
        )

    @staticmethod
    def _top_sync(limit: int) -> list[ProcessInfo]:
        processes: list[ProcessInfo] = []
        for proc in psutil.process_iter(["pid", "name", "memory_info", "memory_percent"]):
            try:
                info = proc.info
                mem = info.get("memory_info")
                if mem is None:  # AccessDenied для чужого процесса
                    continue
                processes.append(
                    ProcessInfo(
                        pid=info["pid"],
                        name=info.get("name") or "?",
                        rss=mem.rss,
                        mem_percent=info.get("memory_percent") or 0.0,
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        processes.sort(key=lambda p: p.rss, reverse=True)
        return processes[:limit]
