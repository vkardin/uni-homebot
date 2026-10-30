"""Конфигурация бота. Все параметры читаются из переменных окружения / `.env`."""

from __future__ import annotations

import logging
import os
import re
from contextlib import suppress
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

_LIST_SPLIT_RE = re.compile(r"[,\s]+")
_TIME_RE = re.compile(r"(?:[01]?\d|2[0-3]):[0-5]\d")


def system_timezone() -> str:
    """Часовой пояс сервера: `TZ`, `/etc/timezone`, ссылка `/etc/localtime`; иначе UTC."""
    candidates = [os.environ.get("TZ", "").lstrip(":")]
    with suppress(OSError):
        candidates.append(Path("/etc/timezone").read_text(encoding="utf-8").strip())
    with suppress(OSError):
        target = str(Path("/etc/localtime").resolve())
        if "/zoneinfo/" in target:
            candidates.append(target.split("/zoneinfo/", 1)[1])
    for name in candidates:
        if not name or name.startswith("/"):
            continue
        with suppress(ZoneInfoNotFoundError, ValueError):
            ZoneInfo(name)
            return name
    return "UTC"


def _split_list(value: str) -> list[str]:
    """`"a, b\\nc"` -> `["a", "b", "c"]` (запятые, пробелы и переводы строк)."""
    return [item for item in _LIST_SPLIT_RE.split(value.strip()) if item]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,  # пустое значение в .env = «не задано»
    )

    # --- Telegram -----------------------------------------------------------
    bot_token: SecretStr
    # Пусто — владелец назначается кодом из журнала при первом запуске (services/owner.py).
    admin_id: int | None = Field(None, gt=0)

    # --- Общее --------------------------------------------------------------
    log_level: str = "INFO"
    # Пусто — часовой пояс сервера (см. system_timezone), а если его не узнать — UTC.
    # pip-пакет `tzdata` из requirements.txt знает и современные имена, и старые алиасы вроде "Europe/Kiev".
    timezone: str = Field(default_factory=system_timezone)
    # Знак валюты по умолчанию для финансов и цен на сайтах (ACTUAL_CURRENCY / SITES_CURRENCY его перекрывают).
    currency: str = ""
    state_dir: Path = Path("data")
    # Бот в Docker (ставит образ): нет systemd, journald, polkit и SMART — функции хоста выключаются сами.
    homebot_in_container: bool = False
    # 📦 Каталог: бот может ставить приложения из белого списка (служба ставится update.sh только при true)
    catalog_enabled: bool = False

    # --- Мониторинг и алерты --------------------------------------------------
    # Через запятую. Имена systemd-юнитов: ssh, tailscaled, ollama, docker ...
    monitored_services: str = "ssh,tailscaled,ollama"
    alerts_enabled: bool = True
    alert_interval_sec: int = Field(60, ge=10)
    alert_cooldown_sec: int = Field(1800, ge=60)
    alert_cpu_percent: float = Field(90.0, gt=0, le=100)
    alert_cpu_consecutive: int = Field(3, ge=1)
    alert_ram_percent: float = Field(90.0, gt=0, le=100)
    alert_disk_percent: float = Field(90.0, gt=0, le=100)
    alert_temp_c: float = Field(85.0, gt=0)

    # --- Ollama ---------------------------------------------------------------
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2"
    ollama_timeout_sec: int = Field(300, ge=5)
    # Сколько держать модель в памяти после запроса: "5m", "30s", "0", "-1".
    ollama_keep_alive: str = "5m"
    ollama_max_history: int = Field(12, ge=2)
    # «Размышления» моделей вроде Qwen3/Qwen3.5: на CPU превращают ответ из секунд в минуты.
    ollama_think: bool = False
    ollama_system_prompt: str = (
        "Ты — UNI, дружелюбный робот-помощник домашнего сервера. Отвечай по-русски, тепло, но коротко и по делу, "
        "без воды. Если не знаешь — честно скажи. Не выдумывай факты."
    )

    # --- Новости каждый час (начальные значения; дальше меняются в боте: ⚙️ Настройки) ---------
    news_enabled: bool = True
    news_interval_min: int = Field(60, ge=15, le=1440)
    news_count: int = Field(5, ge=1, le=10)
    news_quiet_hours: str = "23-8"  # пусто — без тихих часов

    # --- Погода, курсы, источники новостей --------------------------------------
    weather_lat: float | None = Field(None, ge=-90, le=90)
    weather_lon: float | None = Field(None, ge=-180, le=180)
    weather_label: str = "Погода"
    # URL RSS/Atom-лент через запятую или с новой строки.
    rss_feeds: str = ""
    # Курсы НБУ в часовой сводке: коды валют через запятую; пусто = не показывать.
    rates_currencies: str = ""

    # --- История метрик и графики -----------------------------------------------
    sample_interval_sec: int = Field(60, ge=10)
    history_retention_days: int = Field(30, ge=1, le=365)

    # --- Безопасность и обслуживание ---------------------------------------------
    ssh_watch_enabled: bool = True
    ssh_fail_report_sec: int = Field(300, ge=30)
    maintenance_enabled: bool = True

    # --- Управление сервером ------------------------------------------------------
    # Службы, которые можно перезапускать кнопкой (через запятую). Пусто = перезапуск отключён.
    restart_services: str = "ollama"
    # Разрешить кнопки «Перезагрузить» / «Выключить» (с подтверждением). Нужны правила polkit —
    # их создаёт deploy/update.sh.
    power_enabled: bool = False
    # 🛠 Ремонт сервера из бота и панели: по умолчанию выключен; включается только здесь, в .env на сервере,
    # и только вместе с PIN (не короче 4 цифр). Действия — из белого списка, каждое пишется в журнал.
    repair_enabled: bool = False
    repair_pin: SecretStr | None = None

    # --- Приложения (пустой URL или нет доступа = интеграция выключена) ------------------
    docker_url: str = "http://127.0.0.1:2375"  # docker-socket-proxy, только чтение
    docker_restart_enabled: bool = True
    docker_watch_enabled: bool = True
    docker_ignore: str = ""  # контейнеры, о падении которых не сообщать
    speedtest_url: str = "http://127.0.0.1:8084"
    speedtest_token: SecretStr | None = None
    speedtest_min_down_mbps: float = Field(0.0, ge=0)  # 0 = не следить за скоростью, только за сбоями
    qbit_url: str = "http://127.0.0.1:8080"
    qbit_username: str = ""
    qbit_password: SecretStr | None = None
    adguard_url: str = "http://127.0.0.1:8880"
    adguard_username: str = ""
    adguard_password: SecretStr | None = None
    jellyfin_url: str = "http://127.0.0.1:8096"
    jellyfin_api_key: SecretStr | None = None
    jellyfin_tv: str = ""  # часть имени устройства для «📺 На ТВ»; пусто — первый открытый телевизор
    radarr_url: str = "http://127.0.0.1:7878"
    radarr_api_key: SecretStr | None = None
    radarr_profile: str = ""  # профиль качества для новых фильмов; пусто — 1080p, если есть
    sonarr_url: str = "http://127.0.0.1:8989"
    sonarr_api_key: SecretStr | None = None
    sonarr_profile: str = ""  # профиль качества для сериалов; пусто — 1080p, если есть
    cleanup_min_days: int = Field(3, ge=0, le=365)  # просмотренное моложе N дней в «Чистке диска» не предлагаем
    # Actual Budget — через контейнер-обёртку actual-http-api (у самого Actual HTTP API нет)
    actual_url: str = "http://127.0.0.1:5007"
    actual_api_key: SecretStr | None = None
    actual_budget_id: str = ""  # Sync ID бюджета: Actual → Settings → Advanced settings
    actual_account: str = ""  # название или id счёта для трат; пусто — первый открытый счёт
    actual_encryption_password: SecretStr | None = None  # только если бюджет зашифрован
    actual_currency: str = ""  # пусто — CURRENCY
    expense_confirm: bool = True  # перед записью траты показывать карточку с кнопкой «Записать»

    # --- Присмотр за сервером (2.6) --------------------------------------------------
    docker_autoheal: str = ""  # контейнеры, которые бот перезапускает сам (до 3 раз в час), через запятую
    docker_diagnose: bool = True  # к сообщению о проблеме контейнера добавлять разбор журнала моделью
    speedtest_drop_percent: int = Field(50, ge=0, le=95)  # «скорость упала», если ниже N % от обычной; 0 — выключить
    speedtest_weekly_report: bool = True  # воскресный отчёт с графиком
    kernel_watch_enabled: bool = True  # ошибки диска/памяти в журнале ядра

    # --- Игры и погода (2.6) ------------------------------------------------------------
    games_enabled: bool = True
    games_country: str = "US"  # регион магазина Epic (влияет на список и цены)
    games_extra_sources: bool = True  # Steam/GOG раздачи через gamerpower.com
    weather_alerts_enabled: bool = True  # «скоро дождь», заморозки, жара, шквалистый ветер
    weather_frost_c: float = -5.0
    weather_heat_c: float = 35.0
    weather_gust_ms: float = 17.0

    # --- Темы (2.6) -----------------------------------------------------------------------
    # 💱 P2P-курс Binance: за сколько можно продать USDT. Выключен, пока не включат. Настройки уведомлений — в боте (/p2p)
    p2p_enabled: bool = False
    p2p_fiat: str = "UAH"
    p2p_asset: str = "USDT"
    p2p_pay_types: str = ""  # способы оплаты по идентификаторам Binance через запятую: Monobank,PrivatBank. Пусто — любые
    p2p_amount: float = Field(5000.0, ge=0)  # сумма сделки в фиат: объявления, в чьи лимиты она не входит, не учитываются; 0 — любые
    p2p_interval_sec: int = Field(300, ge=60, le=3600)
    p2p_summary_hour: int = Field(21, ge=0, le=23)  # во сколько присылать вечернюю сводку (по часовому поясу бота)
    # 🔎 Сайты: слежка за ценами и страницами через changedetection.io. Ключ — Settings → API (update.sh подставляет сам)
    changedetection_url: str = "http://127.0.0.1:5000"
    changedetection_api_key: SecretStr | None = None
    sites_interval_sec: int = Field(300, ge=60, le=3600)  # как часто бот спрашивает у changedetection, что изменилось
    sites_currency: str = ""  # знак валюты в сообщениях о ценах; пусто — CURRENCY
    sites_browser: bool = False  # новые слежки сразу открывать браузером (нужен браузерный контейнер в changedetection)
    guests_enabled: bool = True  # 👥 принимать заявки от других людей (без одобрения админа доступа нет)
    web_enabled: bool = True  # 🖥 веб-панель (Telegram Mini App)
    web_host: str = "127.0.0.1"  # наружу не открываем: HTTPS даёт Tailscale Serve (см. README)
    web_port: int = Field(8088, ge=1, le=65535)
    web_url: str = ""  # публичный https-адрес панели, например https://myserver.tail1234.ts.net
    voice_model: str = "base"  # модель распознавания речи (faster-whisper): tiny / base / small
    voice_language: str = "ru"  # язык голосовых; пусто — определять автоматически
    voice_max_sec: int = Field(180, ge=10, le=900)
    backup_hour: int = Field(4, ge=0, le=23)  # во сколько делать ежедневный бэкап
    backup_keep: int = Field(7, ge=1, le=60)  # сколько копий хранить
    backup_paths: str = ""  # дополнительные файлы/папки через запятую (например, compose-файлы Docker)
    # лёгкая модель Ollama для ПЕРЕВОДА (заголовки, описания), например qwen3:1.7b; пусто — та же, что в чате
    translate_model: str = ""
    translate_ukrainian: bool = False  # украинские новости тоже переводить на русский
    libretranslate_url: str = ""  # например http://127.0.0.1:5000 — тогда ВСЕ переводы (заголовки, описания) идут через него
    libretranslate_api_key: SecretStr | None = None

    # --- Валидаторы -----------------------------------------------------------
    @model_validator(mode="after")
    def _fill_currency(self) -> Settings:
        """ACTUAL_CURRENCY и SITES_CURRENCY без значения берут общий CURRENCY."""
        # object.__setattr__: значение подставлено, а не задано явно (model_fields_set не трогаем)
        for name in ("actual_currency", "sites_currency"):
            if not getattr(self, name):
                object.__setattr__(self, name, self.currency)
        return self

    @field_validator("admin_id", mode="before")
    @classmethod
    def _admin_placeholder(cls, value: object) -> object:
        """ADMIN_ID=123456789 из старого .env.example — не настоящий ID, а заглушка: считаем, что не задан."""
        return None if str(value).strip() == "123456789" else value

    @field_validator("guests_enabled", mode="before")
    @classmethod
    def _bool_without_comment(cls, value: object) -> object:
        """`GUESTS_ENABLED=true  # комментарий` в одной строке не должен ронять запуск."""
        return value.split("#", 1)[0].strip() if isinstance(value, str) else value

    @field_validator("web_url")
    @classmethod
    def _check_web_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith("https://"):
            raise ValueError("WEB_URL должен начинаться с https:// (Telegram открывает Mini App только по HTTPS) или быть пустым")
        return value

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("ожидается DEBUG, INFO, WARNING, ERROR или CRITICAL")
        return value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"неизвестная тайм-зона: {value!r}") from exc
        return value

    @field_validator(
        "docker_url",
        "speedtest_url",
        "qbit_url",
        "adguard_url",
        "jellyfin_url",
        "radarr_url",
        "sonarr_url",
        "actual_url",
        "changedetection_url",
        "libretranslate_url",
    )
    @classmethod
    def _check_app_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("адрес должен начинаться с http:// или https:// (или быть пустым)")
        return value

    @field_validator("news_quiet_hours")
    @classmethod
    def _check_quiet(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r"(?:[01]?\d|2[0-3])-(?:[01]?\d|2[0-3])", value):
            raise ValueError("ожидается формат ЧЧ-ЧЧ, например 23-8 (или пусто)")
        return value

    @field_validator("ollama_host")
    @classmethod
    def _check_ollama_host(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("адрес должен начинаться с http:// или https://")
        return value

    # --- Производные значения ---------------------------------------------------
    @property
    def services(self) -> list[str]:
        return _split_list(self.monitored_services)

    @property
    def feeds(self) -> list[str]:
        return [url for url in _split_list(self.rss_feeds) if url.startswith(("http://", "https://"))]

    @property
    def docker_ignored(self) -> set[str]:
        return set(_split_list(self.docker_ignore))

    @property
    def docker_autoheal_names(self) -> set[str]:
        return set(_split_list(self.docker_autoheal))

    @property
    def restartable(self) -> list[str]:
        return _split_list(self.restart_services)

    @property
    def currencies(self) -> list[str]:
        return [code.upper() for code in _split_list(self.rates_currencies)]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def p2p_methods(self) -> list[str]:
        return _split_list(self.p2p_pay_types)

    @property
    def weather_enabled(self) -> bool:
        return self.weather_lat is not None and self.weather_lon is not None


# Значения по умолчанию до 5.1: у установок, обновлённых с прежних версий, поведение не должно меняться.
LEGACY_DEFAULTS: dict[str, object] = {
    "timezone": "Europe/Kyiv",
    "currency": "₴",
    "rates_currencies": "USD,EUR",
    "games_country": "UA",
    "p2p_enabled": True,
    "translate_ukrainian": True,
}


def with_legacy_defaults(settings: Settings) -> Settings:
    """Возвращает настройки, где всё, что не задано явно в .env/окружении, берёт прежние значения по умолчанию."""
    update = {name: value for name, value in LEGACY_DEFAULTS.items() if name not in settings.model_fields_set}
    if not update:
        return settings
    if "currency" in update:
        update |= {name: update["currency"] for name in ("actual_currency", "sites_currency") if name not in settings.model_fields_set}
    legacy = settings.model_copy()
    for name, value in update.items():  # object.__setattr__: прежнее значение по умолчанию не считается заданным явно
        object.__setattr__(legacy, name, value)
    return legacy


# Что в контейнере не работает: журнал systemd (SSH, ядро), SMART и apt, службы и питание через polkit.
CONTAINER_OFF: dict[str, object] = {
    "ssh_watch_enabled": False,
    "kernel_watch_enabled": False,
    "maintenance_enabled": False,
    "power_enabled": False,
    "restart_services": "",
    "monitored_services": "",
}
CONTAINER_OFF_TEXT = "журнал ядра, SSH-входы, SMART и обновления пакетов, службы, питание, обновление из чата"


def for_container(settings: Settings) -> Settings:
    """В контейнере выключает функции хоста (см. CONTAINER_OFF); вне контейнера возвращает настройки как есть."""
    return settings.model_copy(update=CONTAINER_OFF) if settings.homebot_in_container else settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # обязательные поля берутся из окружения


# 🩹 Параметры .env с ошибкой, вместо которых взято значение по умолчанию (покажем в сообщении о запуске).
LOAD_PROBLEMS: list[str] = []
_CRITICAL = {"bot_token", "admin_id"}


def load_settings() -> Settings:
    """Как get_settings, но ошибка в необязательном параметре не роняет бота: берётся значение по умолчанию.

    Ошибки в BOT_TOKEN/ADMIN_ID и в проверках, касающихся нескольких полей сразу, по-прежнему останавливают запуск.
    """
    LOAD_PROBLEMS.clear()
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        errors = exc.errors()
        bad = {str(e["loc"][0]) for e in errors if e["loc"]}
        if not bad or bad & _CRITICAL or any(not e["loc"] for e in errors) or not bad <= set(Settings.model_fields):
            raise
        log.warning("Ошибки в .env, беру значения по умолчанию: %s", exc)
    defaults = {name: Settings.model_fields[name].get_default(call_default_factory=True) for name in bad}
    settings = Settings(**defaults)  # type: ignore[call-arg]
    settings.__pydantic_fields_set__.difference_update(bad)  # значение по умолчанию — не «задано в .env»
    LOAD_PROBLEMS.extend(sorted(name.upper() for name in bad))
    return settings
