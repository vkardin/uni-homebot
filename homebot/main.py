"""Запуск бота: конфигурация, сервисы, polling, корректное завершение."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import suppress
from datetime import datetime

import aiohttp
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramServerError, TelegramUnauthorizedError
from aiogram.types import BufferedInputFile
from pydantic import ValidationError

from homebot import __version__, i18n
from homebot.app import build_dispatcher
from homebot.config import Settings, for_container, load_settings, with_legacy_defaults
from homebot.jobs.alerts import AlertsJob
from homebot.jobs.apps_watch import DockerWatchJob, SpeedtestWatchJob, TorrentWatchJob
from homebot.jobs.backup import BackupJob
from homebot.jobs.games_watch import FreeGamesJob
from homebot.jobs.hourly_news import HourlyNewsJob
from homebot.jobs.kernel_watch import KernelWatchJob
from homebot.jobs.maintenance import MaintenanceJob
from homebot.jobs.movies_watch import MoviesWatchJob
from homebot.jobs.p2p_watch import P2PWatchJob
from homebot.jobs.reminders import RemindersJob
from homebot.jobs.sampler import SamplerJob
from homebot.jobs.sites_watch import SitesWatchJob
from homebot.jobs.ssh_watch import SshWatchJob
from homebot.jobs.weather_watch import WeatherAlertsJob
from homebot.logging_setup import setup_logging
from homebot.owner_claim import wait_owner
from homebot.services import connect, modules, overrides, owner
from homebot.services.access import AccessService
from homebot.services.apps import Apps, build_apps
from homebot.services.article_translate import LibreTranslator
from homebot.services.backup import parse_extra_paths
from homebot.services.brief import BriefService
from homebot.services.charts import render_speed_chart
from homebot.services.convert import Converter
from homebot.services.db import Database
from homebot.services.digest import DigestService
from homebot.services.free_games import GamesService
from homebot.services.home import HomeService
from homebot.services.inbox import Inbox
from homebot.services.lang import set_translate_ukrainian
from homebot.services.logs import LogsService
from homebot.services.network import NetworkService
from homebot.services.news import NewsService
from homebot.services.ollama import OllamaService
from homebot.services.power import PowerService
from homebot.services.prefs import Prefs
from homebot.services.repair import Repair
from homebot.services.smart import SmartService
from homebot.services.ssh_log import SshLog
from homebot.services.state import StateStore
from homebot.services.system import SystemService
from homebot.services.tasks import TaskManager
from homebot.services.units import UnitsService
from homebot.services.updates import UpdatesService
from homebot.services.weather_alerts import Thresholds, fetch_nowcast
from homebot.ui.keyboards import docker_alert_kb, p2p_alert_kb, service_alert_kb, sites_alert_kb, speedtest_kb
from homebot.utils.inbox_middleware import InboxMiddleware
from homebot.utils.quiet_middleware import QuietHoursMiddleware
from homebot.web import server as web_server
from homebot.web.api import Ctx as WebCtx

log = logging.getLogger("homebot")

# Код выхода «ошибка конфигурации» (EX_CONFIG из sysexits.h). В .service-файле он указан в
# RestartPreventExitStatus, чтобы systemd не крутил бесконечный перезапуск с неверным токеном.
EX_CONFIG = 78


def run() -> int:
    try:
        return asyncio.run(_main())
    except KeyboardInterrupt:
        return 0


def _print_config_errors(exc: ValidationError) -> None:
    print("Ошибка конфигурации (.env / переменные окружения):", file=sys.stderr)
    for error in exc.errors():
        name = ".".join(str(part) for part in error["loc"]).upper() or "?"
        print(f"  - {name}: {error['msg']}", file=sys.stderr)


async def _wait_for_telegram(bot: Bot) -> None:
    """Ждёт доступности Telegram API (после перезагрузки сеть/Tailscale поднимаются не сразу).

    Неверный токен (`TelegramUnauthorizedError`) пробрасывается сразу — ждать бессмысленно.
    """
    delay = 2.0
    while True:
        try:
            me = await bot.get_me()
        except (TelegramNetworkError, TelegramServerError) as exc:
            log.warning("Telegram API unavailable (%s); retry in %.0f s", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)
        else:
            log.info("Authorized as @%s", me.username)
            return


def _machine_translator(settings: Settings, http: aiohttp.ClientSession) -> LibreTranslator | None:
    """🌐 LibreTranslate для всех переводов (заголовки, описания) — модель остаётся только для пересказа."""
    if not settings.libretranslate_url:
        return None
    key = settings.libretranslate_api_key.get_secret_value() if settings.libretranslate_api_key is not None else ""
    return LibreTranslator(http, settings.libretranslate_url, key)


def _register_jobs(
    tasks: TaskManager,
    *,
    bot: Bot,
    http: aiohttp.ClientSession,
    settings: Settings,
    store: StateStore,
    db: Database,
    system: SystemService,
    units: UnitsService,
    smart: SmartService,
    updates: UpdatesService,
    apps: Apps,
    briefs: BriefService,
    prefs: Prefs,
    news: NewsService,
    ollama: OllamaService,
    home: HomeService,
) -> None:
    admin = settings.admin_id

    def is_night() -> bool:
        return prefs.night().is_quiet(datetime.now(settings.tz).hour)

    tasks.register("home", "Стартовый экран", "", home.run, hidden=True)  # погода/курс/расход для меню
    hourly = HourlyNewsJob(bot=bot, admin_id=admin, briefs=briefs, prefs=prefs, tz=settings.tz)
    tasks.register("news", "Сводка", "", hourly.run, hidden=True)  # вкл/выкл — в ⚙️ Настройки → Новости

    # Служебные задачи: работают всегда, в списке кнопок не показываются.
    sampler = SamplerJob(db=db, system=system, interval=settings.sample_interval_sec, retention_days=settings.history_retention_days)
    tasks.register("sampler", "История метрик", "", sampler.run, hidden=True)
    tasks.register("reminders", "Напоминания", "", RemindersJob(bot=bot, admin_id=admin, db=db, tz=settings.tz).run, hidden=True)

    restartable_units = PowerService(settings.restartable, power_enabled=settings.power_enabled).restartable
    log_units = LogsService([*settings.services, "homebot"]).units  # тот же список и порядок, что у кнопок «Журналы»
    alerts = AlertsJob(
        bot=bot,
        admin_id=admin,
        settings=settings,
        system=system,
        units=units,
        markup=lambda failed: service_alert_kb(failed, log_units, restartable_units),
        llm_busy=lambda: ollama.busy,
    )
    tasks.register(
        "alerts",
        "Алерты",
        f"Проверка каждые {settings.alert_interval_sec} с: CPU, RAM, диски, температура, упавшие службы",
        alerts.run,
        default_enabled=settings.alerts_enabled,
    )
    ssh = SshWatchJob(bot=bot, admin_id=admin, report_every=settings.ssh_fail_report_sec)
    tasks.register(
        "ssh",
        "SSH-входы",
        "Сразу сообщает о каждом входе по SSH, неудачные попытки — сводкой",
        ssh.run,
        default_enabled=settings.ssh_watch_enabled,
    )
    maintenance = MaintenanceJob(bot=bot, admin_id=admin, store=store, smart=smart, updates=updates)
    tasks.register(
        "maintenance",
        "Обслуживание",
        "Проблемы SMART и обновления безопасности / необходимость перезагрузки",
        maintenance.run,
        default_enabled=settings.maintenance_enabled,
    )
    if apps.docker is not None:
        docker_watch = DockerWatchJob(
            bot=bot,
            admin_id=admin,
            docker=apps.docker,
            store=store,
            ignore=settings.docker_ignored,
            asker=ollama if settings.docker_diagnose else None,
            autoheal=settings.docker_autoheal_names,
            markup=docker_alert_kb,
        )
        tasks.register(
            "docker",
            "Docker",
            "Сообщает, если контейнер остановился, перезапускается по кругу или стал unhealthy: "
            "с журналом, кнопками и разбором причины",
            docker_watch.run,
            default_enabled=settings.docker_watch_enabled,
        )
    if apps.speedtest is not None:

        async def speed_chart(points):
            return await asyncio.to_thread(render_speed_chart, points, tz=settings.tz, title="Скорость интернета · неделя")

        async def send_photo(png: bytes, caption: str) -> bool:
            try:
                await bot.send_photo(admin, BufferedInputFile(png, "speed.png"), caption=caption)
            except TelegramAPIError as exc:
                log.warning("Cannot deliver speed chart: %s", exc)
                return False
            return True

        speed_watch = SpeedtestWatchJob(
            bot=bot,
            admin_id=admin,
            speedtest=apps.speedtest,
            store=store,
            min_down=settings.speedtest_min_down_mbps,
            drop_percent=settings.speedtest_drop_percent,
            tz=settings.tz,
            weekly=settings.speedtest_weekly_report,
            chart=speed_chart,
            photo=send_photo,
            markup=speedtest_kb,
        )
        limit = f"или скорость ниже {settings.speedtest_min_down_mbps:.0f} Мбит/с" if settings.speedtest_min_down_mbps else ""
        if settings.speedtest_drop_percent:
            limit += f" или упала более чем на {settings.speedtest_drop_percent}% от обычной"
        tasks.register(
            "speedtest",
            "Скорость интернета",
            f"Сообщает, если замер не удался {limit}".strip(),
            speed_watch.run,
            default_enabled=True,
        )
    quiet: set[str] = set()
    if apps.radarr is not None or apps.sonarr is not None:
        movies = MoviesWatchJob(bot=bot, admin_id=admin, store=store, radarr=apps.radarr, sonarr=apps.sonarr)
        tasks.register(
            "movies",
            "Кино",
            "🍿 Фильм или новые серии скачаны — с кнопкой «на ТВ»; сообщает, если раздач не нашлось",
            movies.run,
            default_enabled=True,
        )
        quiet = {"movies", "radarr"} if apps.radarr is not None else set()
        quiet |= {"shows", "tv-sonarr", "sonarr", "tv"} if apps.sonarr is not None else set()
    if apps.qbit is not None:
        torrent_watch = TorrentWatchJob(bot=bot, admin_id=admin, qbit=apps.qbit, store=store, quiet_categories=frozenset(quiet))
        tasks.register(
            "torrents",
            "Торренты",
            "Сообщает о завершённых загрузках (фильмы и сериалы — в разделе «Кино»)",
            torrent_watch.run,
            default_enabled=True,
        )

    if apps.p2p is not None:
        p2p_job = P2PWatchJob(
            bot=bot,
            admin_id=admin,
            service=apps.p2p,
            store=store,
            prefs=prefs.p2p,
            tz=settings.tz,
            interval=settings.p2p_interval_sec,
            summary_hour=settings.p2p_summary_hour,
            markup=p2p_alert_kb,
        )
        tasks.register(
            "p2p",
            "P2P-курс",
            f"💱 Binance P2P: резкое изменение курса, целевая цена и вечерний итог ({apps.p2p.asset}→{apps.p2p.fiat}, /p2p)",
            p2p_job.run,
            default_enabled=settings.p2p_enabled,
        )

    if apps.sites is not None:
        sites_job = SitesWatchJob(
            bot=bot,
            admin_id=admin,
            service=apps.sites,
            store=store,
            interval=settings.sites_interval_sec,
            currency=settings.sites_currency,
            markup=sites_alert_kb,
        )
        tasks.register(
            "sites",
            "Сайты",
            "🔎 Цены и изменения на отслеживаемых страницах (changedetection.io): было→стало, что поменялось (/sites)",
            sites_job.run,
            default_enabled=True,
        )

    if settings.games_enabled:
        games = GamesService(http, country=settings.games_country, extra_sources=settings.games_extra_sources)
        games_job = FreeGamesJob(bot=bot, admin_id=admin, store=store, fetch=games.fetch, tz=settings.tz, is_night=is_night)
        tasks.register(
            "games",
            "Бесплатные игры",
            "🎮 Раздачи Epic Games (и Steam/GOG): сообщает о новой раздаче и за сутки до конца",
            games_job.run,
            default_enabled=True,
        )
    if settings.weather_alerts_enabled and settings.weather_lat is not None and settings.weather_lon is not None:
        lat, lon = settings.weather_lat, settings.weather_lon

        async def nowcast():
            return await fetch_nowcast(http, lat, lon)

        weather_job = WeatherAlertsJob(
            bot=bot,
            admin_id=admin,
            store=store,
            prefs=prefs,
            fetch=nowcast,
            tz=settings.tz,
            limits=Thresholds(settings.weather_frost_c, settings.weather_heat_c, settings.weather_gust_ms),
        )
        tasks.register(
            "weather",
            "Погода: события",
            "☔ Скоро дождь, заморозки, жара или сильный ветер — только когда есть повод",
            weather_job.run,
            default_enabled=True,
        )
    backup_job = BackupJob(
        bot=bot,
        admin_id=admin,
        db=db,
        store=store,
        state_dir=settings.state_dir,
        hour=settings.backup_hour,
        keep=settings.backup_keep,
        extra=parse_extra_paths(settings.backup_paths),
        tz=settings.tz,
    )
    tasks.register("backup", "Бэкап", "", backup_job.run, hidden=True)
    if settings.update_channel != "off" and not settings.homebot_in_container:
        from homebot.jobs.release_watch import ReleaseWatchJob

        watch = ReleaseWatchJob(
            bot=bot, admin_id=admin, store=store, http=http, repo=settings.update_repo, channel=settings.update_channel
        )
        tasks.register(
            "releases", "Новые версии", "🆕 Раз в сутки проверяет релизы UNI на GitHub", watch.run, default_enabled=True
        )  # расписание и кнопки — в ⚙️ Настройки → Бэкап
    if settings.kernel_watch_enabled:
        kernel_job = KernelWatchJob(bot=bot, admin_id=admin, store=store)
        tasks.register(
            "kernel",
            "Журнал ядра",
            "💽 Ошибки диска и файловой системы, OOM, перегрев — раньше, чем это увидит SMART",
            kernel_job.run,
            default_enabled=True,
        )


KEY_DEFAULTS_PROFILE = "defaults_profile"


async def _apply_defaults_profile(settings: Settings, store: StateStore) -> Settings:
    """Установка, обновлённая с версии до 5.1, сохраняет прежние значения по умолчанию (Киев, ₴, P2P…).

    Профиль определяется один раз и запоминается в state.json: были данные бота до первого запуска 5.1 —
    «legacy», иначе (новая установка) — «neutral». Явные значения из .env главнее профиля в обоих случаях.
    """
    profile = store.get(KEY_DEFAULTS_PROFILE)
    if profile not in ("legacy", "neutral"):
        had_data = bool(store.snapshot()) or (settings.state_dir / "homebot.db").exists()
        profile = "legacy" if had_data else "neutral"
        await store.set(KEY_DEFAULTS_PROFILE, profile)
        log.info("Defaults profile: %s", profile)
    return with_legacy_defaults(settings) if profile == "legacy" else settings


async def _main() -> int:
    try:
        settings = load_settings()  # 🩹 ошибка в необязательном параметре не роняет бота
    except ValidationError as exc:
        _print_config_errors(exc)
        return EX_CONFIG

    setup_logging(settings.log_level)
    log.info("HomeBot %s starting", __version__)

    store = StateStore(settings.state_dir / "state.json")
    store.load()
    settings = await _apply_defaults_profile(settings, store)
    settings = overrides.apply(settings, overrides.load(settings.state_dir))  # ⚙️ настройки из панели, .env главнее
    if store.get("overrides_pending"):
        await store.set("overrides_pending", False)  # перезапуск уже применил сохранённое
    settings = for_container(connect.apply(settings, connect.load(settings.state_dir)))  # 🧩 приложения из чата
    set_translate_ukrainian(settings.translate_ukrainian)
    i18n.set_lang(settings.language)
    db = Database(settings.state_dir / "homebot.db")
    try:
        db.open()
    except Exception as exc:  # sqlite3.Error, OSError
        log.critical("Cannot open database %s: %s", settings.state_dir / "homebot.db", exc)
        return 1

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    if settings.admin_id is None:  # мастер первого запуска: владелец по коду из журнала
        owner_id = owner.load(settings.state_dir)
        if owner_id is None:
            try:
                await _wait_for_telegram(bot)
                owner_id = await wait_owner(bot, settings.state_dir)
            except TelegramUnauthorizedError:
                log.critical("Telegram отклонил токен — проверьте BOT_TOKEN")
                with suppress(Exception):
                    await bot.session.close()
                db.close()
                return EX_CONFIG
        object.__setattr__(settings, "admin_id", owner_id)

    # Единый HTTP-клиент для Ollama, погоды и RSS; таймауты задаются на каждый запрос отдельно.
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
        headers={"User-Agent": f"homebot/{__version__}"},
    ) as http:
        system = SystemService(settings.tz)
        units = UnitsService(settings.services)
        ollama = OllamaService(
            http,
            store,
            host=settings.ollama_host,
            default_model=settings.ollama_model,
            request_timeout=settings.ollama_timeout_sec,
            keep_alive=settings.ollama_keep_alive,
            max_history=settings.ollama_max_history,
            think=settings.ollama_think,
            system_prompt=settings.ollama_system_prompt,
        )
        coords = (
            (settings.weather_lat, settings.weather_lon)
            if settings.weather_lat is not None and settings.weather_lon is not None
            else None
        )
        smart = SmartService(settings.state_dir / "smart.json")
        updates = UpdatesService()
        apps = build_apps(http, settings)
        modules.apply(apps, modules.off(store))  # 🧱 выключенные плитки
        prefs = Prefs(store, settings)
        inbox = Inbox(settings.state_dir)
        bot.session.middleware(InboxMiddleware(inbox, settings.admin_id))  # 🔔 копия фоновых уведомлений для панели
        bot.session.middleware(QuietHoursMiddleware(prefs, settings.tz))  # 🌙 ночью обычные уведомления — без звука
        machine = _machine_translator(settings, http)
        news = NewsService(
            http,
            db,
            ollama,
            translator=machine,
            translate_model=prefs.translate_model,
        )
        log.info("Connected apps: %s", ", ".join(apps.available()) or "none")
        digest = DigestService(
            http,
            system,
            units,
            tz=settings.tz,
            weather_coords=coords,
            weather_label=settings.weather_label,
            currencies=settings.currencies,
            updates=updates,
            smart=smart,
        )
        briefs = BriefService(digest=digest, news=news, db=db, prefs=prefs, store=store, tz=settings.tz)
        actual = apps.actual

        async def month_spent() -> int | None:
            if actual is None:
                return None
            return (await actual.budget_month(datetime.now(settings.tz).strftime("%Y-%m"))).spent

        home = HomeService(
            session=http,
            db=db,
            system=system,
            coords=coords,
            currencies=settings.currencies,
            month_spent=month_spent if actual is not None else None,
        )
        tasks = TaskManager(store)
        _register_jobs(
            tasks,
            bot=bot,
            http=http,
            settings=settings,
            store=store,
            db=db,
            system=system,
            units=units,
            smart=smart,
            updates=updates,
            apps=apps,
            briefs=briefs,
            prefs=prefs,
            news=news,
            ollama=ollama,
            home=home,
        )

        for job in modules.off_jobs(modules.off(store)):  # 🧱 задачи выключенных разделов не запускаем
            tasks.unregister(job)

        power_service = PowerService(settings.restartable, power_enabled=settings.power_enabled)
        logs_service = LogsService([*settings.services, "homebot"])
        network_service = NetworkService(http)

        repair = Repair(settings, power_service, tasks)  # 🛠 ремонт сервера (выключен, пока нет REPAIR_ENABLED и PIN)
        access = AccessService(store, settings.admin_id) if settings.guests_enabled else None
        converter = Converter(settings.state_dir)
        dp = build_dispatcher(
            admin_id=settings.admin_id,
            access=access,
            settings=settings,
            http=http,
            db=db,
            system=system,
            units=units,
            ollama=ollama,
            tasks=tasks,
            network=network_service,
            ssh_log=SshLog(settings.tz),
            updates=updates,
            smart=smart,
            power=power_service,
            logs=logs_service,
            apps=apps,
            news=news,
            briefs=briefs,
            prefs=prefs,
            store=store,
            home=home,
            converter=converter,
            repair=repair,
        )

        web_runner = await web_server.start(
            WebCtx(
                settings=settings, db=db, store=store, system=system, units=units, power=power_service, logs=logs_service,
                apps=apps, tasks=tasks, ollama=ollama, news=news, network=network_service, prefs=prefs, access=access,
                converter=converter, http=http, inbox=inbox, repair=repair,
            )
        )  # fmt: skip

        try:
            await _wait_for_telegram(bot)
        except TelegramUnauthorizedError:
            log.critical("Telegram отклонил токен — проверьте BOT_TOKEN")
            with suppress(Exception):
                await bot.session.close()
            return EX_CONFIG

        try:
            # Накопившиеся за время простоя нажатия кнопок не выполняем: «выгрузить модель»
            # или «перезапустить» через несколько часов — это сюрприз, а не помощь.
            await bot.delete_webhook(drop_pending_updates=True)
            # SIGINT/SIGTERM (systemctl stop) aiogram обрабатывает сам: polling останавливается,
            # вызываются shutdown-хуки (остановка фоновых задач), затем закрываются сессии.
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        finally:
            if web_runner is not None:
                with suppress(Exception):
                    await web_runner.cleanup()
            with suppress(Exception):
                await bot.session.close()
            db.close()

    log.info("HomeBot stopped")
    return 0
