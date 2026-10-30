"""Запуск веб-панели внутри процесса бота."""

from __future__ import annotations

import logging

from aiohttp import web

from homebot.web.api import Ctx, build_app

log = logging.getLogger(__name__)


async def start(ctx: Ctx) -> web.AppRunner | None:
    settings = ctx.settings
    if not settings.web_enabled:
        return None
    runner = web.AppRunner(build_app(ctx), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, settings.web_host, settings.web_port).start()
    except OSError as exc:
        log.error("Веб-панель не запустилась на %s:%s: %s", settings.web_host, settings.web_port, exc)
        await runner.cleanup()
        return None
    log.info("Веб-панель: http://%s:%s (открывать через HTTPS-адрес из WEB_URL)", settings.web_host, settings.web_port)
    return runner
