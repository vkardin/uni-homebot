"""7.0: языки интерфейса."""

from __future__ import annotations

import pytest

from homebot import i18n
from homebot.config import Settings
from homebot.services import overrides


@pytest.fixture(autouse=True)
def _reset():
    yield
    i18n.set_lang("ru")


def test_tr_fallback():
    assert i18n.tr("🗞 Новости") == "🗞 Новости"
    assert i18n.set_lang("en") == "en" and i18n.tr("🗞 Новости") == "🗞 News"
    assert i18n.tr("Нет перевода") == "Нет перевода"
    i18n.set_lang("uk")
    assert i18n.tr("Главное меню") == "Головне меню"
    assert i18n.set_lang("xx") == "ru"


def test_menu_and_commands_translated():
    from homebot.app import COMMANDS, visible_commands
    from homebot.ui import keyboards as kb

    i18n.set_lang("en")
    texts = {b.text for row in kb.main_menu().inline_keyboard for b in row}
    assert "⚙️ Settings" in texts
    descs = {c.command: c.description for c in visible_commands(COMMANDS, set())}
    assert descs["menu"] == "Main menu" and all(1 <= len(d) <= 256 for d in descs.values())


def test_setting():
    assert Settings(bot_token="1:x", _env_file=None, language="EN").language == "en"  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Settings(bot_token="1:x", _env_file=None, language="de")  # type: ignore[call-arg]
    assert overrides.validate("language", "Uk") == "uk"
    with pytest.raises(ValueError):
        overrides.validate("language", "fr")
