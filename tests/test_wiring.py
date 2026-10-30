"""Smoke-тесты связки с aiogram: импорты, DI-имена обработчиков, callback_data, клавиатуры.

Требуют установленный aiogram (`pip install -r requirements.txt`), иначе пропускаются.
Именно эти проверки ловят опечатки, которые иначе вылезли бы только при первом нажатии кнопки.
"""

import inspect
from pathlib import Path

import pytest

pytest.importorskip("aiogram.fsm.storage.memory")

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup

from homebot.app import COMMANDS, build_dispatcher
from homebot.handlers import build_routers
from homebot.services.backup import BackupFile
from homebot.services.tasks import TaskInfo
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, MenuCb, OllamaAction, OllamaCb, TaskCb

# Всё, что aiogram может подставить в обработчик + наши workflow_data (см. build_dispatcher / main).
INJECTABLE = {
    "state", "raw_state", "bot", "bots", "dispatcher", "callback_data", "command", "event_update",
    "event_from_user", "event_chat", "event_router", "handler",
    "settings", "system", "units", "ollama", "tasks",
    "http", "db", "network", "ssh_log", "updates", "smart", "power", "logs", "apps", "news", "prefs", "store", "briefs", "home", "access", "guest", "converter", "repair",
}  # fmt: skip


INJECTABLE_SERVICES = (
    "settings", "system", "units", "ollama", "tasks",
    "http", "db", "network", "ssh_log", "updates", "smart", "power", "logs", "apps", "news", "prefs", "store", "briefs", "home", "converter", "repair",
)  # fmt: skip


def _callbacks(router):
    for observer in router.observers.values():
        for handler in observer.handlers:
            yield observer.event_name, handler.callback


def test_all_routers_build_with_unique_names_and_fallback_last():
    routers = build_routers()
    names = [r.name for r in routers]
    assert len(set(names)) == len(names)
    assert names[-1] == "fallback" and names[-2] == "errors"
    from homebot.app import GUEST_COMMANDS

    assert COMMANDS and all(c.command.islower() for c in COMMANDS)
    assert GUEST_COMMANDS and {c.command for c in GUEST_COMMANDS} <= {c.command for c in COMMANDS} | {"cancel", "note", "remind"}


def test_handler_parameters_are_injectable():
    for router in build_routers():
        for event_name, callback in _callbacks(router):
            params = list(inspect.signature(callback).parameters)[1:]  # первый — само событие
            unknown = [p for p in params if p not in INJECTABLE]
            assert not unknown, f"{router.name}.{callback.__name__} ({event_name}) requests unknown {unknown}"


def test_dispatcher_builds_and_hooks_have_injectable_params():
    dp = build_dispatcher(admin_id=1, **{name: None for name in INJECTABLE_SERVICES})
    for observer in (dp.startup, dp.shutdown):
        for handler in observer.handlers:
            assert set(inspect.signature(handler.callback).parameters) <= INJECTABLE


def _walk_buttons(markup: InlineKeyboardMarkup):
    for row in markup.inline_keyboard:
        yield from row


def test_callback_data_roundtrip_and_size_limit():
    samples: list[CallbackData] = [MenuCb(action=a) for a in MenuAction]
    samples += [OllamaCb(action=a, idx=i) for a in OllamaAction for i in (-1, 0, 29)]
    samples += [TaskCb(name="alerts"), TaskCb(name="digest")]
    for sample in samples:
        packed = sample.pack()
        assert len(packed.encode()) <= 64, packed
        assert type(sample).unpack(packed) == sample


def _all_callback_classes():
    from homebot.ui import callbacks

    return [
        obj for obj in vars(callbacks).values() if isinstance(obj, type) and issubclass(obj, CallbackData) and obj is not CallbackData
    ]


def test_callback_prefixes_are_unique():
    prefixes = [cls.__prefix__ for cls in _all_callback_classes()]
    assert len(prefixes) == len(set(prefixes)), prefixes


def test_keyboards_use_valid_callback_data():
    from datetime import UTC, datetime

    from homebot.services.cleanup import Watched
    from homebot.services.db import Note, Reminder
    from homebot.services.docker_api import Container
    from homebot.services.prefs import NewsPrefs, NightPrefs, P2PPrefs
    from homebot.services.radarr import Film
    from homebot.services.sites import Site
    from homebot.services.sonarr import Show
    from homebot.ui.callbacks import App, MovieAction, MovieCb, PowerAction

    SITE_ID = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"
    films = [Film(99999999, "Фильм"), Film(5, "В Radarr", radarr_id=3), Film(6, "Скачан", radarr_id=4, has_file=True)]
    shows = [Show(99999999, "Сериал"), Show(7, "В Sonarr", sonarr_id=1, episodes_have=2, episodes_total=10)]
    tasks = [TaskInfo("alerts", "Алерты", "d", True), TaskInfo("digest", "Дайджест", "d", False)]
    models = [f"very-long-model-name-{n}:70b-instruct-q4_K_M" for n in range(25)]
    feeds = [f"https://feeds{n}.example-very-long-domain-name.com/rss/all/news.xml" for n in range(30)]
    containers = [Container(f"container-name-{n}", "img", "running", "Up") for n in range(25)]
    containers.append(Container("x" * 40, "img", "exited", "Exited (1)"))  # длинное имя — без кнопки
    from homebot.services.access import Person

    people = [
        Person(9_999_999_999_999, "Очень длинное имя " * 6, "user", status, ("notes", "p2p"), 1_800_000_000)
        for status in ("pending", "approved", "blocked")
    ]
    markups = [
        kb.tools_menu_kb(merge=True, download=True), kb.tools_ops_kb(5, [type("O", (), {"label": "→ JPG"})()] * 3),
        kb.tools_collect_kb(2), kb.tools_link_kb(ffmpeg=True),
        kb.main_menu(), kb.server_menu(), kb.status_kb(), kb.live_kb(), kb.top_kb(), kb.charts_kb("24h"),
        kb.services_kb(), kb.logs_menu_kb(["ssh", "ollama"]), kb.log_view_kb(1), kb.refresh_kb(MenuAction.NETWORK),
        kb.power_kb(["ollama", "docker"], power_enabled=True), kb.confirm_kb(PowerAction.REBOOT, -1, int(datetime.now(UTC).timestamp())),
        kb.tasks_kb(tasks), kb.ollama_kb(), kb.chat_kb(), kb.models_kb(models, models[3]),
        kb.reminders_kb([Reminder(10**9, 1_800_000_000, "текст " * 20)]), kb.notes_kb([Note(10**9, 1, "заметка " * 20)]),
        kb.apps_kb(list(App)), kb.docker_kb(containers), kb.container_kb("nginx-proxy-manager", restart=True),
        kb.container_confirm_kb("nginx-proxy-manager", 1_800_000_000), kb.speedtest_kb(), kb.qbit_kb(),
        kb.adguard_kb(protection=True), kb.adguard_kb(protection=False), kb.app_refresh_kb(App.JELLYFIN),
        kb.news_kb(), kb.brief_kb(), kb.news_models_kb(models, models[1]), kb.settings_kb(), kb.news_settings_kb(NewsPrefs(feeds=tuple(feeds))), kb.feeds_kb(feeds),
        kb.presets_kb(set(feeds)), kb.back_kb(),
        kb.movies_kb(), kb.moods_kb(), kb.movie_cancel_kb(),
        kb.films_kb([(kb.card_cb(f), "x" * 80) for f in films] + [(kb.card_cb(sh), "сериал") for sh in shows], again=MovieCb(action=MovieAction.MOOD, arg="gem")),
        *(kb.film_kb(f, playable=p, resume=r, downloading=d) for f in films for p in (True, False) for r in (True, False) for d in (True, False)),
        *(kb.show_kb(sh, playable=p, downloading=d) for sh in shows for p in (True, False) for d in (True, False)),
        kb.movie_ready_kb(99999999), kb.movie_ready_kb(99999999, series=True), kb.movie_retry_kb(1), kb.movie_retry_kb(1, series=True),
        kb.cleanup_kb([Watched("m", 99999999, "Длинное название " * 5, 2024, 10**11, 5), Watched("s", 7, "Сериал", None, 5, None)]),
        kb.cleanup_confirm_kb("m", 99999999, 1_800_000_000), kb.cleanup_all_confirm_kb(8, "a1b2c3d4e5f6", 1_800_000_000),
        kb.docker_alert_kb(["container-name-1", "x" * 40], True), kb.docker_alert_kb(["web"], False),
        kb.finance_kb("Очень длинное название счёта в банке"), kb.finance_back_kb(), kb.expense_kb(99999), kb.expense_saved_kb(99999),
        kb.expense_categories_kb(99999, [f"Категория номер {n}" for n in range(40)]),
        kb.expense_accounts_kb(99999, ["Карта", "Наличные", "Накопления"]), kb.default_account_kb(["Карта"]), kb.entry_cancel_kb(),
        kb.ops_kb(99999, 10), kb.ops_kb(99999, 0), kb.delete_confirm_kb(99999, 9), kb.budget_kb(0), kb.budget_kb(-24),
        kb.night_kb(NightPrefs()), kb.night_kb(NightPrefs(enabled=False)), kb.translate_models_kb(models, models[2]), kb.doctor_kb(),
        kb.p2p_kb(P2PPrefs()), kb.p2p_kb(P2PPrefs(0.0, 41.55, False)), kb.p2p_cancel_kb(), kb.p2p_alert_kb(),
        kb.sites_kb([Site(f"{n:08x}-aaaa-bbbb-cccc-{n:012x}", "https://olx.ua/d/x", "Очень длинное название товара " * 3, mode="price") for n in range(12)]),
        kb.sites_kb([]), kb.voice_kb(1, remind=True, spend=True), kb.voice_kb(2, remind=False, spend=False), kb.webapp_kb("https://x.ts.net"), kb.backup_kb([]), kb.backup_kb([BackupFile(Path('/x/homebot-20260101-040000.tar.gz'), 1_800_000_000, 5000)] * 7), kb.backup_confirm_kb(1_800_000_000), kb.sites_cancel_kb(), kb.sites_mode_kb(), kb.site_kb(Site(SITE_ID, "https://olx.ua/d/x")), kb.site_kb(Site(SITE_ID, "https://a.ua/x", paused=True)), kb.site_kb(Site(SITE_ID, "https://a.ua/x", mode="price", browser=True), has_target=True), kb.site_kb(Site(SITE_ID, "https://a.ua/x", mode="price")),
        kb.site_confirm_kb(SITE_ID, 1_800_000_000), kb.sites_alert_kb(Site(SITE_ID, "https://olx.ua/d/x")),
        kb.update_kb(previous="3.2.0"), kb.update_kb(previous=None), kb.update_confirm_kb("a1b2c3d4e5f6", 1_800_000_000, can_install=True),
        kb.update_confirm_kb("a1b2c3d4e5f6", 1_800_000_000, can_install=False), kb.rollback_confirm_kb(1_800_000_000),
        kb.guest_menu(["notes", "reminders", "p2p", "news"]), kb.guest_menu(["notes"], "https://x.ts.net"), kb.guest_menu([], "https://x.ts.net"), kb.access_list_kb(people), kb.access_list_kb([]),
        kb.person_kb(people[0]), kb.person_kb(people[1]), kb.person_kb(people[2]), kb.access_request_kb(9_999_999_999_999),
        kb.access_remove_kb(9_999_999_999_999),
    ]  # fmt: skip
    parsers = _all_callback_classes()
    for markup in markups:
        for button in _walk_buttons(markup):
            if button.web_app:
                assert button.web_app.url.startswith("https://") and not button.callback_data
                continue
            if button.url:  # кнопка-ссылка (страница магазина): без callback_data
                assert button.url.startswith(("http://", "https://")) and not button.callback_data
                continue
            data = button.callback_data
            assert data and len(data.encode()) <= 64, data
            owner = [p for p in parsers if data.startswith(p.__prefix__ + ":")]
            assert len(owner) == 1, data
            owner[0].unpack(data)


def test_router_filters_follow_aiogram_calling_convention():
    """aiogram вызывает фильтр как filter(event, **data): событие — позиционно, `guest` — по имени."""
    import asyncio
    from types import SimpleNamespace

    from homebot.handlers.guest import is_guest, not_guest

    person = SimpleNamespace(id=5)
    for fn in (is_guest, not_guest):
        assert inspect.iscoroutinefunction(fn)
    assert asyncio.run(is_guest(object(), guest=person)) and not asyncio.run(is_guest(object(), guest=None))
    assert asyncio.run(not_guest(object(), guest=None)) and not asyncio.run(not_guest(object(), guest=person))
    assert asyncio.run(not_guest(object()))  # данных о госте нет (например, события вне middleware) — админская ветка не закрывается


def test_main_menu_gets_panel_button_only_with_https_url():
    try:
        kb.set_panel_url("http://insecure")
        assert not [b for b in _walk_buttons(kb.main_menu()) if b.web_app]
        kb.set_panel_url("https://x.ts.net")
        buttons = list(_walk_buttons(kb.main_menu()))
        assert buttons[0].web_app and buttons[0].web_app.url == "https://x.ts.net" and len(buttons) == 12
    finally:
        kb.set_panel_url("")
