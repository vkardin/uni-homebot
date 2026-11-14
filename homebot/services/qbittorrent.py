"""qBittorrent WebUI API v2: скорости, список торрентов, пауза/продолжение всех."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

from homebot.services.apps_http import AppError, describe_status

DOWNLOADING_STATES = {"downloading", "forcedDL", "metaDL", "stalledDL", "queuedDL", "checkingDL", "allocating"}
PAUSED_STATES = {"pausedDL", "pausedUP", "stoppedDL", "stoppedUP"}


@dataclass(frozen=True, slots=True)
class Torrent:
    hash: str
    name: str
    progress: float  # 0..1
    state: str
    dlspeed: int
    upspeed: int
    eta: int
    size: int
    category: str = ""

    @property
    def done(self) -> bool:
        return self.progress >= 1.0

    @property
    def emoji(self) -> str:
        if self.state in PAUSED_STATES:
            return "⏸"
        if self.state == "error" or self.state == "missingFiles":
            return "⚠️"
        if self.done:
            return "✅"
        if self.state in DOWNLOADING_STATES:
            return "⬇️"
        return "•"


@dataclass(frozen=True, slots=True)
class Transfer:
    dl_speed: int
    up_speed: int
    dl_session: int
    up_session: int


def parse_torrents(data: Any) -> list[Torrent]:
    if not isinstance(data, list):
        raise AppError("неожиданный ответ qBittorrent")
    result = []
    for item in data:
        if not isinstance(item, dict) or "hash" not in item:
            continue
        result.append(
            Torrent(
                hash=str(item["hash"]),
                name=str(item.get("name", "?")),
                progress=float(item.get("progress") or 0),
                state=str(item.get("state", "")),
                dlspeed=int(item.get("dlspeed") or 0),
                upspeed=int(item.get("upspeed") or 0),
                eta=int(item.get("eta") or 0),
                size=int(item.get("size") or item.get("total_size") or 0),
                category=str(item.get("category") or ""),
            )
        )
    return result


LOGIN_COOLDOWN = 300.0  # с: после отказа во входе не стучимся снова — иначе qBittorrent банит IP на час


class QbitService:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, username: str, password: str) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._sid: str | None = None
        self._login_lock = asyncio.Lock()
        self._blocked_until = 0.0
        self._blocked_reason = ""
        # Почему не вошли в последний раз: "" — всё хорошо, "auth" — логин/пароль, "address" — по адресу не qBittorrent,
        # "down" — адрес не отвечает. Для любого из них /doctor предложит мастер «адрес, логин, пароль».
        self.login_problem = ""

    @property
    def auth_failed(self) -> bool:
        return bool(self.login_problem)

    @property
    def base_url(self) -> str:
        return self._base

    @property
    def username(self) -> str:
        return self._username

    def set_credentials(self, username: str, password: str, base_url: str | None = None) -> None:
        """Новые адрес, логин и пароль (проверенные `check_login`) — без перезапуска бота."""
        if base_url:
            self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._sid = None
        self._blocked_until = 0.0
        self._blocked_reason = ""
        self.login_problem = ""

    async def check_login(self, username: str, password: str, base_url: str | None = None) -> None:
        """Один пробный вход с другими данными; текущий вход не трогает. AppError — с понятной причиной."""
        probe = QbitService(self._session, base_url or self._base, username, password)
        await probe._try_login()

    async def _is_qbit(self) -> bool | None:
        """Отвечает ли по адресу именно qBittorrent (его страница входа); None — проверить не вышло."""
        try:
            async with self._session.get(f"{self._base}/", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                page = (await resp.read())[:20000].decode(errors="replace").lower()
        except (aiohttp.ClientError, TimeoutError, UnicodeError):
            return None
        return "qbittorrent" in page or "vuetorrent" in page

    async def _login(self) -> None:
        async with self._login_lock:
            if time.monotonic() < self._blocked_until:  # недавно отказали — повторять вход бессмысленно и опасно
                raise AppError(self._blocked_reason)
            try:
                await self._try_login()
            except AppError as exc:
                text = str(exc)
                self.login_problem = (
                    "auth" if "неверный логин" in text else "address" if "не qBittorrent" in text
                    else "down" if "недоступен" in text else ""
                )  # fmt: skip
                if "заблокировал" in str(exc) or "неверный логин" in str(exc):
                    self._blocked_until = time.monotonic() + LOGIN_COOLDOWN
                    self._blocked_reason = f"{exc} — повторю вход через несколько минут"
                raise
            self._blocked_until = 0.0
            self.login_problem = ""

    async def _try_login(self) -> None:
        try:
            async with self._session.post(
                f"{self._base}/api/v2/auth/login",
                data={"username": self._username, "password": self._password},
                headers={"Referer": self._base},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                body = (await resp.text()).strip()
                bad_login = "неверный логин или пароль qBittorrent (QBIT_USERNAME/QBIT_PASSWORD)"
                wrong = f"по адресу {self._base} отвечает не qBittorrent — проверьте QBIT_URL"
                if resp.status in (404, 405):
                    raise AppError(wrong)
                if resp.status == 401 or (resp.status < 400 and body not in ("Ok.", "")):
                    # 401 и «Fails.» — отказ во входе, но так же отвечает и чужой сервис на этом порту
                    raise AppError(wrong if await self._is_qbit() is False else bad_login)
                if resp.status >= 400:
                    raise AppError(
                        "qBittorrent заблокировал вход (много неудачных попыток?)"
                        if resp.status == 403
                        else describe_status(resp.status)
                    )
                cookie = resp.cookies.get("SID")
                if body not in ("Ok.", "") or cookie is None:  # старые версии: «Ok.» / «Fails.», новые: 204 и пустое тело
                    raise AppError(bad_login)
                self._sid = cookie.value
        except AppError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise AppError(f"qBittorrent недоступен по адресу {self._base} — он запущен? Адрес — QBIT_URL") from exc

    async def _call(self, method: str, path: str, *, params: dict[str, str] | None = None, data: dict[str, str] | None = None) -> Any:
        for attempt in range(2):
            if self._sid is None:
                await self._login()
            try:
                async with self._session.request(
                    method,
                    f"{self._base}{path}",
                    params=params or {},
                    data=data,
                    headers={"Cookie": f"SID={self._sid}", "Referer": self._base},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 403 and attempt == 0:
                        self._sid = None  # сессия истекла — войдём заново
                        continue
                    if resp.status >= 400:
                        raise AppError(describe_status(resp.status))
                    text = await resp.text()
            except AppError:
                raise
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise AppError("qBittorrent недоступен") from exc
            if not text.strip():
                return None
            try:
                return json.loads(text)
            except ValueError:
                return text
        raise AppError("не удалось авторизоваться в qBittorrent")

    async def transfer(self) -> Transfer:
        data = await self._call("GET", "/api/v2/transfer/info")
        if not isinstance(data, dict):
            raise AppError("неожиданный ответ qBittorrent")
        return Transfer(
            dl_speed=int(data.get("dl_info_speed") or 0),
            up_speed=int(data.get("up_info_speed") or 0),
            dl_session=int(data.get("dl_info_data") or 0),
            up_session=int(data.get("up_info_data") or 0),
        )

    async def torrents(self) -> list[Torrent]:
        return parse_torrents(await self._call("GET", "/api/v2/torrents/info", params={"sort": "added_on", "reverse": "true"}))

    async def add_magnet(self, magnet: str) -> None:
        reply = await self._call("POST", "/api/v2/torrents/add", data={"urls": magnet})
        if isinstance(reply, str) and reply.strip().lower().startswith("fail"):
            raise AppError("qBittorrent не принял ссылку (возможно, такая раздача уже есть)")

    async def _all(self, new_verb: str, old_verb: str) -> None:
        # qBittorrent 5.x переименовал pause/resume в stop/start; поддерживаем обе версии.
        try:
            await self._call("POST", f"/api/v2/torrents/{new_verb}", data={"hashes": "all"})
        except AppError as exc:
            if "404" not in str(exc):
                raise
            await self._call("POST", f"/api/v2/torrents/{old_verb}", data={"hashes": "all"})

    async def pause_all(self) -> None:
        await self._all("stop", "pause")

    async def resume_all(self) -> None:
        await self._all("start", "resume")
