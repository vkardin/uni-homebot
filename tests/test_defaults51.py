"""5.1: нейтральные значения по умолчанию, прежние — для обновлённых установок, разделы меню по подключённым приложениям."""

import asyncio
import tempfile
from pathlib import Path

from aiogram.types import BotCommand

from homebot.app import COMMANDS, visible_commands
from homebot.config import Settings, system_timezone, with_legacy_defaults
from homebot.main import KEY_DEFAULTS_PROFILE, _apply_defaults_profile
from homebot.services import tiles
from homebot.services.apps import Apps
from homebot.services.state import StateStore
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction


def make(**overrides) -> Settings:
    return Settings(_env_file=None, **{"bot_token": "123:abc", "admin_id": 42, **overrides})  # type: ignore[call-arg]


def test_neutral_defaults():
    s = make()
    assert s.timezone == system_timezone()
    assert s.currency == s.actual_currency == s.sites_currency == ""
    assert s.currencies == [] and s.games_country == "US"
    assert not s.p2p_enabled and not s.translate_ukrainian


def test_currency_fills_app_currencies_unless_set():
    s = make(currency="€")
    assert s.actual_currency == s.sites_currency == "€"
    s = make(currency="€", sites_currency="$")
    assert s.actual_currency == "€" and s.sites_currency == "$"


def test_legacy_defaults_keep_old_behaviour_but_respect_explicit_values():
    s = with_legacy_defaults(make())
    assert s.timezone == "Europe/Kyiv" and s.actual_currency == s.sites_currency == "₴"
    assert s.currencies == ["USD", "EUR"] and s.games_country == "UA" and s.p2p_enabled and s.translate_ukrainian
    s = with_legacy_defaults(make(timezone="UTC", p2p_enabled=False, sites_currency="$"))
    assert s.timezone == "UTC" and not s.p2p_enabled and s.sites_currency == "$" and s.actual_currency == "₴"


def _profile(state: dict | None, db: bool = False) -> tuple[Settings, StateStore]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = StateStore(root / "state.json")
        if state is not None:
            asyncio.run(store.replace_all(state))
        if db:
            (root / "homebot.db").write_bytes(b"")
        settings = asyncio.run(_apply_defaults_profile(make(state_dir=root), store))
        return settings, store


def test_profile_detection():
    settings, store = _profile(None)
    assert store.get(KEY_DEFAULTS_PROFILE) == "neutral" and not settings.p2p_enabled
    settings, store = _profile({"tasks": {"news": True}})
    assert store.get(KEY_DEFAULTS_PROFILE) == "legacy" and settings.timezone == "Europe/Kyiv"
    settings, store = _profile(None, db=True)
    assert store.get(KEY_DEFAULTS_PROFILE) == "legacy"
    # однажды решённый профиль не меняется
    settings, store = _profile({KEY_DEFAULTS_PROFILE: "neutral", "tasks": {}})
    assert store.get(KEY_DEFAULTS_PROFILE) == "neutral" and not settings.p2p_enabled


def _actions(markup) -> set[str]:
    return {b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data}


def test_menu_hides_unconnected_sections():
    try:
        kb.set_connected_tiles(set())
        hidden = _actions(kb.main_menu())
        guest = _actions(kb.guest_menu(["p2p", "notes"]))
        kb.set_connected_tiles({"movies", "finance", "p2p", "sites"})
        full = _actions(kb.main_menu())
    finally:
        kb.set_connected_tiles(set(tiles.BY_ID))
    optional = {kb._menu(a).pack() for a in (MenuAction.MOVIES, MenuAction.FINANCE, MenuAction.P2P, MenuAction.SITES)}
    assert optional <= full and not optional & hidden
    assert kb._menu(MenuAction.NEWS).pack() in hidden
    assert kb._menu(MenuAction.P2P).pack() not in guest and kb._menu(MenuAction.NOTES).pack() in guest


def test_commands_follow_sections():
    names = {c.command for c in visible_commands(COMMANDS, set())}
    assert not names & {"movies", "money", "spend", "p2p", "sites"} and "menu" in names
    assert {c.command for c in visible_commands(COMMANDS, {"p2p"})} >= {"p2p"}
    assert visible_commands([BotCommand(command="x", description="y")], set())[0].command == "x"


def test_tiles_registry():
    assert tiles.connected_ids(Apps()) == set()
    assert tiles.connected_ids(Apps(p2p=object(), sonarr=object())) == {"p2p", "movies"}  # type: ignore[arg-type]
    assert len(tiles.BY_ID) == len(tiles.TILES)  # id уникальны
    commands = [c for t in tiles.TILES for c in t.commands]
    assert len(commands) == len(set(commands))  # команды уникальны
    assert {t.menu for t in tiles.MENU_TILES} == set(kb.TILE_SECTIONS)  # у каждой плитки с разделом есть кнопка
    apps_fields = set(Apps.__dataclass_fields__)
    assert all(set(t.services) <= apps_fields for t in tiles.TILES)
    described = {d["id"]: d for d in tiles.describe(Apps(docker=object()))}  # type: ignore[arg-type]
    assert described["docker"]["connected"] and not described["movies"]["connected"]


def test_container_mode_turns_off_host_features():
    from homebot.config import for_container

    s = make(power_enabled=True, restart_services="ollama")
    assert for_container(s) is s
    c = for_container(make(homebot_in_container=True, power_enabled=True, restart_services="ollama"))
    assert not (c.power_enabled or c.ssh_watch_enabled or c.kernel_watch_enabled or c.maintenance_enabled)
    assert c.restartable == [] and c.services == []
