"""🧩 Плитки: реестр того, что можно подключить к UNI (приложения дома и внешние сервисы).

Описание плитки — тот же формат, что станет манифестом модуля в 6.0: кто она, что ей нужно из настроек,
какие команды и кнопку меню она даёт и как понять, что она подключена. Меню, список команд и /doctor
строятся отсюда: неподключённой плитки в интерфейсе нет.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homebot.services.apps import Apps


@dataclass(frozen=True, slots=True)
class Tile:
    id: str
    title: str
    icon: str
    needs: tuple[str, ...]  # параметры .env, без которых плитка не включится
    services: tuple[str, ...]  # поля Apps; плитка подключена, если есть хотя бы одно
    menu: str = ""  # раздел главного меню (пусто — живёт в «🖥 Сервер → Приложения»)
    commands: tuple[str, ...] = ()

    def connected(self, apps: Apps) -> bool:
        return any(getattr(apps, name, None) is not None for name in self.services)


TILES: tuple[Tile, ...] = (
    Tile("movies", "Кино", "🎬", ("RADARR_API_KEY", "SONARR_API_KEY"), ("radarr", "sonarr"), menu="movies", commands=("movies",)),
    Tile("finance", "Финансы", "💰", ("ACTUAL_API_KEY", "ACTUAL_BUDGET_ID"), ("actual",), menu="finance", commands=("money", "spend")),
    Tile("p2p", "P2P курс", "💱", ("P2P_ENABLED",), ("p2p",), menu="p2p", commands=("p2p",)),
    Tile("sites", "Сайты", "🔎", ("CHANGEDETECTION_API_KEY",), ("sites",), menu="sites", commands=("sites",)),
    Tile("docker", "Docker", "🐳", ("DOCKER_URL",), ("docker",), commands=("docker",)),
    Tile("speedtest", "Speedtest", "🚀", ("SPEEDTEST_TOKEN",), ("speedtest",), commands=("speed",)),
    Tile("qbit", "qBittorrent", "🧲", ("QBIT_USERNAME", "QBIT_PASSWORD"), ("qbit",), commands=("torrents",)),
    Tile("adguard", "AdGuard", "🛡", ("ADGUARD_USERNAME", "ADGUARD_PASSWORD"), ("adguard",)),
    Tile("jellyfin", "Jellyfin", "📺", ("JELLYFIN_API_KEY",), ("jellyfin",)),
)
BY_ID: dict[str, Tile] = {tile.id: tile for tile in TILES}
MENU_TILES: tuple[Tile, ...] = tuple(tile for tile in TILES if tile.menu)


def connected_ids(apps: Apps) -> set[str]:
    return {tile.id for tile in TILES if tile.connected(apps)}


def hidden_commands(connected: set[str]) -> set[str]:
    """Команды плиток, которые не подключены (другие плитки с той же командой её не спасают — команды уникальны)."""
    return {cmd for tile in TILES if tile.id not in connected for cmd in tile.commands}


def describe(apps: Apps) -> list[dict[str, Any]]:
    """Список плиток для панели и /doctor: id, название, иконка, подключена ли, что нужно для подключения."""
    return [{"id": t.id, "title": t.title, "icon": t.icon, "connected": t.connected(apps), "needs": list(t.needs)} for t in TILES]


@dataclass(frozen=True, slots=True)
class Field:
    name: str  # поле Settings
    label: str  # что спросить у человека
    secret: bool = False


@dataclass(frozen=True, slots=True)
class Connector:
    """Как подключить приложение из чата: где его искать среди контейнеров и какие параметры спросить."""

    service: str  # поле Apps
    title: str
    images: tuple[str, ...]  # подстроки имени образа Docker (в нижнем регистре)
    port: int  # порт приложения внутри контейнера и по умолчанию
    url_field: str  # поле Settings с адресом; пусто — адрес не нужен
    fields: tuple[Field, ...]
    enable_field: str = ""  # булево поле Settings, которое включает интеграцию (P2P)


CONNECTORS: tuple[Connector, ...] = (
    Connector(
        "radarr", "Radarr", ("radarr",), 7878, "radarr_url", (Field("radarr_api_key", "API-ключ: Settings → General → API Key", True),)
    ),
    Connector(
        "sonarr", "Sonarr", ("sonarr",), 8989, "sonarr_url", (Field("sonarr_api_key", "API-ключ: Settings → General → API Key", True),)
    ),
    Connector(
        "jellyfin",
        "Jellyfin",
        ("jellyfin",),
        8096,
        "jellyfin_url",
        (Field("jellyfin_api_key", "API-ключ: Панель управления → API-ключи → «+»", True),),
    ),
    Connector(
        "qbit",
        "qBittorrent",
        ("qbittorrent",),
        8080,
        "qbit_url",
        (Field("qbit_username", "логин веб-интерфейса"), Field("qbit_password", "пароль веб-интерфейса", True)),
    ),
    Connector(
        "adguard",
        "AdGuard Home",
        ("adguardhome", "adguard"),
        3000,
        "adguard_url",
        (Field("adguard_username", "логин панели AdGuard"), Field("adguard_password", "пароль панели AdGuard", True)),
    ),
    Connector(
        "speedtest",
        "Speedtest Tracker",
        ("speedtest-tracker",),
        80,
        "speedtest_url",
        (Field("speedtest_token", "API-токен: /admin/api-tokens (Read Results + Run Speedtest)", True),),
    ),
    Connector(
        "sites",
        "changedetection.io",
        ("changedetection",),
        5000,
        "changedetection_url",
        (Field("changedetection_api_key", "API-ключ: Settings → API", True),),
    ),
    Connector(
        "actual",
        "Actual Budget",
        ("actual-http-api", "actual_http_api"),
        5007,
        "actual_url",
        (
            Field("actual_api_key", "ключ API_KEY контейнера actual-http-api", True),
            Field("actual_budget_id", "Sync ID бюджета: Settings → Advanced"),
        ),
    ),
    Connector(
        "p2p",
        "Binance P2P",
        (),
        0,
        "",
        (Field("p2p_fiat", "код валюты, за которую продаёте USDT: UAH, EUR, USD…"),),
        enable_field="p2p_enabled",
    ),
)
CONNECTOR_BY_SERVICE: dict[str, Connector] = {c.service: c for c in CONNECTORS}
