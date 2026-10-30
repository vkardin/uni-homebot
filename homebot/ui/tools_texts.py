"""Тексты раздела «🧰 Инструменты»."""

from __future__ import annotations

from homebot.services.convert import KIND_TITLE, Caps, describe_caps, install_hint
from homebot.ui.style import DIV
from homebot.ui.texts import esc


def size_text(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} МБ"
    return f"{max(size, 1) // 1024 or 1} КБ"


def render_tools_menu(caps: Caps, *, admin: bool, download: bool) -> str:
    lines = [
        "🧰 <b>Инструменты</b>",
        DIV,
        "Просто <b>пришлите файл</b> — я предложу, во что его превратить:",
        "• 🖼 картинки и фото с iPhone → JPG, PNG, WebP, PDF, сжать, текст с фото",
        "• 📕 PDF → текст, страницы в картинки, сжать",
        "• 📄 Word, Excel, PowerPoint → PDF",
        "• 🎵 аудио и 🎬 видео → MP3, голосовое, сжатие, GIF, кадр",
        "",
        "📑 <b>Собрать PDF</b> из нескольких фото или файлов — кнопка ниже.",
    ]
    if download:
        lines.append("⬇️ <b>По ссылке</b> — скачать звук или видео (до 50 МБ).")
    lines.append("")
    lines.append("<i>Файлы до 20 МБ. Для исходного качества присылайте фото «как файл».</i>")
    if admin:
        missing = [f"{name} ({what})" for ok, name, what in describe_caps(caps) if not ok]
        if missing:
            lines += [DIV, "⚪ Не установлено: " + esc(", ".join(missing))]
            if hint := install_hint(caps):
                lines.append(f"Поставить: <code>{esc(hint)}</code>")
    return "\n".join(lines)


def render_ops_prompt(kind: str, name: str, size: int, *, photo: bool, none: bool = False) -> str:
    head = f"{KIND_TITLE.get(kind, '📎 Файл')} <b>{esc(name[:80])}</b> · {size_text(size)}"
    if none:
        return f"{head}\n\nС этим файлом на сервере пока ничего не сделать: не хватает программ. Администратору: /doctor."
    tail = "\n<i>Фото приходят уже пережатыми — для исходного качества шлите «как файл».</i>" if photo else ""
    return f"{head}\n\nВо что превратить?{tail}"


UNKNOWN_FILE = "🤷 С таким файлом ничего не умею. Понимаю картинки, PDF, документы Office (Word, Excel, PowerPoint), аудио и видео."
TOO_BIG = "📦 Файл больше 20 МБ — Telegram не разрешает боту такие скачивать. Сожмите или обрежьте и пришлите снова."
EXPIRED = "Этот файл уже устарел — пришлите его заново"
BUSY = "⏳ Сейчас идёт другая конвертация — подожду свою очередь…"
QUOTA = "⏱ Вы много конвертировали за последний час — попробуйте позже."
MERGE_HELP = (
    f"📑 <b>Сборка PDF</b>\n{DIV}\nПрисылайте по порядку фото (лучше «как файл») и PDF — до 20 штук. "
    "Когда всё отправлено, нажмите «Собрать»."
)
LINK_HELP = (
    f"⬇️ <b>Скачивание по ссылке</b>\n{DIV}\nПришлите ссылку на видео или трек (YouTube и другие сайты). "
    "Скачиваю только для личного пользования, до 50 МБ."
)
