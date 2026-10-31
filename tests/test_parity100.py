"""10.0: паритет чата и панели. Каждый раздел есть и командой в чате, и экраном или API в панели.

Добавили экран в панель — добавьте команду (и строку сюда), иначе тест упадёт.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "homebot"
APP_JS = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
API = (ROOT / "web" / "api.py").read_text(encoding="utf-8")
HANDLERS = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "handlers").glob("*.py"))
CHAT_COMMANDS = {c for group in re.findall(r"Command\(([^)]*)\)", HANDLERS) for c in re.findall(r'"([a-z0-9_]+)"', group)}
PANEL_SUBS = set(re.findall(r"^\s+(\w+): \[\"", APP_JS[APP_JS.index("const SUBS = {") : APP_JS.index("const SUB_TILE")], re.M))
PANEL_SUBS |= set(re.findall(r",\s*(\w+): \[\"", APP_JS[APP_JS.index("const SUBS = {") : APP_JS.index("const SUB_TILE")]))

# раздел панели (SUBS в app.js) → команда в чате
PARITY = {
    "settings": "settings",
    "tools": "tools",
    "movies": "movies",
    "sites": "sites",
    "house": "home",
    "rules": "rules",
    "servers": "servers",
    "people": "access",
    "modules": "modules",
    "community": "catalog",
    "reminders": "reminders",
    "notes": "notes",
    "ai": "ollama",
    "backup": "backup",
}
# основные вкладки панели → команда и API
TABS = {"server": ("status", "/api/overview"), "news": ("news", "/api/news"), "money": ("money", "/api/finance")}


def test_every_panel_section_has_chat_command():
    assert PANEL_SUBS, "не нашёл SUBS в app.js"
    missing = PANEL_SUBS - set(PARITY)
    assert not missing, f"в панели есть разделы без команды в чате: {missing}"
    for sub, command in PARITY.items():
        assert sub in PANEL_SUBS, f"раздела {sub} больше нет в панели — уберите его из PARITY"
        assert command in CHAT_COMMANDS, f"нет команды /{command} для раздела {sub}"


def test_tabs():
    for command, api in TABS.values():
        assert command in CHAT_COMMANDS
        assert f'"{api}' in API


def test_new_platform_commands_have_panel_side():
    for command, api in {"report": "/api/report", "why": "/api/ask-server", "modules": "/api/plugins", "rules": "/api/rules",
                         "servers": "/api/servers", "home": "/api/hass", "catalog": "/api/community"}.items():  # fmt: skip
        assert command in CHAT_COMMANDS and f'"{api}' in API
