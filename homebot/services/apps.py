"""Реестр подключённых приложений: каждое включается, только если в .env заданы его адрес и доступ."""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from homebot.config import Settings
from homebot.services.actual import ActualService
from homebot.services.adguard import AdGuardService
from homebot.services.docker_api import DockerService
from homebot.services.hass import HassService
from homebot.services.jellyfin import JellyfinService
from homebot.services.p2p import P2PService
from homebot.services.qbittorrent import QbitService
from homebot.services.radarr import RadarrService
from homebot.services.sites import SitesService
from homebot.services.sonarr import SonarrService
from homebot.services.speedtest import SpeedtestService


@dataclass(slots=True)
class Apps:
    docker: DockerService | None = None
    speedtest: SpeedtestService | None = None
    qbit: QbitService | None = None
    adguard: AdGuardService | None = None
    jellyfin: JellyfinService | None = None
    radarr: RadarrService | None = None
    sonarr: SonarrService | None = None
    actual: ActualService | None = None
    p2p: P2PService | None = None  # у него свой экран (/p2p), в список «Приложения» не входит
    sites: SitesService | None = None  # changedetection.io: свой экран (/sites)
    hass: HassService | None = None  # 🏠 Home Assistant: экран «Дом» в панели

    def available(self) -> list[str]:
        return [
            name
            for name in ("docker", "speedtest", "qbit", "adguard", "jellyfin", "radarr", "sonarr", "actual", "hass")
            if getattr(self, name)
        ]


def build_apps(session: aiohttp.ClientSession, s: Settings) -> Apps:
    secret = lambda value: value.get_secret_value() if value is not None else ""  # noqa: E731
    apps = Apps()
    if s.docker_url:
        apps.docker = DockerService(session, s.docker_url, restart_enabled=s.docker_restart_enabled)
    if s.speedtest_url and s.speedtest_token is not None:
        apps.speedtest = SpeedtestService(session, s.speedtest_url, secret(s.speedtest_token))
    if s.qbit_url and s.qbit_username and s.qbit_password is not None:
        apps.qbit = QbitService(session, s.qbit_url, s.qbit_username, secret(s.qbit_password))
    if s.adguard_url and s.adguard_username and s.adguard_password is not None:
        apps.adguard = AdGuardService(session, s.adguard_url, s.adguard_username, secret(s.adguard_password))
    if s.jellyfin_url and s.jellyfin_api_key is not None:
        apps.jellyfin = JellyfinService(session, s.jellyfin_url, secret(s.jellyfin_api_key))
    if s.radarr_url and s.radarr_api_key is not None:
        apps.radarr = RadarrService(session, s.radarr_url, secret(s.radarr_api_key), profile=s.radarr_profile)
    if s.sonarr_url and s.sonarr_api_key is not None:
        apps.sonarr = SonarrService(session, s.sonarr_url, secret(s.sonarr_api_key), profile=s.sonarr_profile)
    if s.actual_url and s.actual_api_key is not None and s.actual_budget_id:
        apps.actual = ActualService(
            session,
            s.actual_url,
            secret(s.actual_api_key),
            s.actual_budget_id,
            account=s.actual_account,
            encryption_password=secret(s.actual_encryption_password),
        )
    if s.changedetection_url and s.changedetection_api_key is not None:
        apps.sites = SitesService(session, s.changedetection_url, secret(s.changedetection_api_key))
    if s.hass_url and s.hass_token is not None:
        apps.hass = HassService(session, s.hass_url, secret(s.hass_token))
    if s.p2p_enabled:
        apps.p2p = P2PService(session, fiat=s.p2p_fiat, asset=s.p2p_asset, pay_types=s.p2p_methods, amount=s.p2p_amount)
    return apps
