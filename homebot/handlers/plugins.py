"""🧩 Команды внешних модулей и /modules — список модулей с правами и статусом."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from homebot import plugins

router = Router(name="plugins")


def _plugin_command(message: Message) -> bool:
    text = message.text or ""
    if not text.startswith("/") or plugins.REGISTRY is None:
        return False
    command = text[1:].split(maxsplit=1)[0].split("@", 1)[0].lower() if len(text) > 1 else ""
    return command in plugins.REGISTRY.commands


@router.message(Command("modules"))
async def cmd_modules(message: Message) -> None:
    reg = plugins.REGISTRY
    items = list(reg.plugins.values()) if reg else []
    if not items:
        await message.answer(
            "🧩 <b>Внешние модули</b>\n\nПока нет ни одного. Модуль — папка в <code>STATE_DIR/modules/</code> "
            "с module.json и module.py (см. docs/MODULES.md). Включаются в панели: Ещё → Модули."
        )
        return
    lines = ["🧩 <b>Внешние модули</b>", ""]
    for p in items:
        m = p.manifest
        mark = "🟢" if p.loaded else ("🔴" if p.error else "⚪")
        cmds = " ".join(f"/{c}" for c, _ in m.commands)
        lines.append(f"{mark} <b>{html.escape(m.name)}</b> {html.escape(m.version)} {cmds}")
        if p.error:
            lines.append(f"     <i>{html.escape(p.error)}</i>")
    lines += ["", "Включить или выключить: панель → Ещё → Модули."]
    await message.answer("\n".join(lines))


@router.message(F.text, _plugin_command)
async def on_plugin_command(message: Message) -> None:
    assert plugins.REGISTRY is not None
    text = message.text or ""
    head, _, args = text[1:].partition(" ")
    reply = await plugins.REGISTRY.run_command(head.split("@", 1)[0].lower(), args.strip())
    if reply:
        await message.answer(reply)
