"""🆕 Новые версии на GitHub: бот сам узнаёт о релизе и по кнопке скачивает архив (и подпись, если есть).

Канал `UPDATE_CHANNEL`: stable — только обычные релизы, beta — и тестовые (pre-release), off — не проверять.
Ставит обновление по-прежнему обновлятор и только после «✅ Установить».
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homebot.services.selfupdate import compare_versions

log = logging.getLogger(__name__)
API = "https://api.github.com/repos/{repo}/releases?per_page=10"
_ASSET_RE = re.compile(r"^homebot-([0-9][0-9A-Za-z.+-]*)\.tar\.gz$")
MAX_ASSET = 20 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Release:
    version: str
    url: str  # архив
    sig_url: str  # подпись; пусто — нет
    page: str
    prerelease: bool


def parse(data: Any) -> list[Release]:
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        assets = {a.get("name"): a.get("browser_download_url") for a in item.get("assets") or [] if isinstance(a, dict)}
        for name, url in assets.items():
            m = _ASSET_RE.match(str(name or ""))
            if m and isinstance(url, str) and url.startswith("https://"):
                sig = assets.get(f"{name}.sig")
                out.append(Release(m.group(1), url, sig if isinstance(sig, str) else "", str(item.get("html_url", "")),
                                   bool(item.get("prerelease"))))  # fmt: skip
                break
    return out


def newest(releases: list[Release], current: str, channel: str) -> Release | None:
    allowed = [r for r in releases if channel == "beta" or not r.prerelease]
    best = max(allowed, key=lambda r: tuple(int(x) for x in re.findall(r"\d+", r.version)[:4]), default=None)
    return best if best is not None and compare_versions(best.version, current) > 0 else None


async def fetch(http: Any, repo: str) -> list[Release]:
    import aiohttp

    async with http.get(API.format(repo=repo), timeout=aiohttp.ClientTimeout(total=20),
                        headers={"Accept": "application/vnd.github+json"}) as resp:  # fmt: skip
        resp.raise_for_status()
        return parse(await resp.json())


async def download(http: Any, url: str, target: Path) -> None:
    import aiohttp

    size = 0
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        async with http.get(url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
            resp.raise_for_status()
            with tmp.open("wb") as fh:
                async for chunk in resp.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > MAX_ASSET:
                        raise ValueError("файл больше 20 МБ")
                    fh.write(chunk)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
