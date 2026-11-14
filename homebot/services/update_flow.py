"""⬆️ Обновление бота из панели: те же проверки, что и в чате (подпись, системные файлы, бэкап перед установкой)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from homebot import __version__
from homebot.services import backup as backup_svc
from homebot.services import releases, selfupdate, signing

log = logging.getLogger(__name__)


def status(state_dir: Path) -> dict[str, Any]:
    """Что сейчас с обновлением: версия, обновлятор, прежняя версия, итог последней установки."""
    return {
        "version": __version__,
        "ready": selfupdate.updater_installed(),
        "previous": selfupdate.previous_version(),
        "pending": selfupdate.request_pending(state_dir),
        "result": selfupdate.read_result(state_dir),
    }


async def latest(http: Any, repo: str, channel: str) -> releases.Release | None:
    """Новый релиз на GitHub (или None, если стоит самый свежий)."""
    return releases.newest(await releases.fetch(http, repo), __version__, channel)


def blocker(info: selfupdate.ArchiveInfo, archive: Path, require_signature: bool) -> str | None:
    """Почему этот архив ставить нельзя; None — можно."""
    if info.system_changed:
        return "в этой версии изменились системные файлы — нужна обычная установка (deploy/update.sh)"
    sign = signing.status(archive, info.sha256)
    if sign == signing.BAD or (require_signature and sign != signing.SIGNED):
        return "подпись автора не подтверждена — установка запрещена"
    return None


async def download(http: Any, repo: str, version: str, state_dir: Path) -> selfupdate.ArchiveInfo:
    """Скачать релиз `version` с GitHub в папку обновлятора и проверить архив."""
    found = {r.version: r for r in await releases.fetch(http, repo)}
    rel = found.get(version)
    if rel is None:
        raise ValueError("релиз не найден")
    target = selfupdate.archive_path(state_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    signing.sig_path(target).unlink(missing_ok=True)  # подпись от прежнего архива не подходит
    await releases.download(http, rel.url, target)
    if rel.sig_url:
        await releases.download(http, rel.sig_url, signing.sig_path(target))
    return await asyncio.to_thread(selfupdate.inspect_archive, target)


async def backup_before(settings: Any, store: Any, db: Any) -> None:
    """💾 Копия базы и состояния; не получилось — обновление всё равно идёт (откат кода есть)."""
    try:
        await backup_svc.run_backup(settings.state_dir, db, store, keep=settings.backup_keep + 1, extra=[], tz=settings.tz)
    except backup_svc.BackupError as exc:
        log.warning("Backup before update failed: %s", exc)
