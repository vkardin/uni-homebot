"""🧰 Инструменты: конвертер файлов, сборка PDF из фото, скачивание по ссылке.

Роутер общий для администратора и гостей с правом «tools» (скачивание по ссылке — отдельное право «download»),
поэтому стоит ПЕРЕД роутером гостей и сам проверяет доступ фильтром `tools_ok`.
Все тяжёлые операции идут по одной (блокировка в `Converter`), гостям добавлен лимит «в час».
"""

from __future__ import annotations

import logging
import re
import tempfile
import time
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramEntityTooLarge
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from homebot.services.access import Person
from homebot.services.convert import (
    MAX_INPUT,
    MAX_MERGE_BYTES,
    MAX_MERGE_FILES,
    Converter,
    ConvertError,
    Result,
    kind_of,
    ops_for,
)
from homebot.services.urlguard import public_http_url
from homebot.states import ToolsStates
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, MenuCb, ToolAction, ToolCb
from homebot.ui.texts import esc
from homebot.ui.tools_texts import (
    BUSY,
    EXPIRED,
    LINK_HELP,
    MERGE_HELP,
    QUOTA,
    TOO_BIG,
    UNKNOWN_FILE,
    render_ops_prompt,
    render_tools_menu,
    size_text,
)
from homebot.utils.telegram import edit_or_send

log = logging.getLogger(__name__)
router = Router(name="tools")

GUEST_PER_HOUR = 15  # конвертаций в час на одного гостя
JOB_TTL = 3600
MAX_JOBS = 300
URL_RE = re.compile(r"https?://\S+")


async def tools_ok(event: Any, guest: Person | None = None) -> bool:
    """Администратору можно всегда, гостю — только с правом «tools»."""
    return guest is None or guest.can("tools")


router.message.filter(tools_ok)
router.callback_query.filter(tools_ok)


def can_download(guest: Person | None, converter: Converter) -> bool:
    return converter.caps.ytdlp and (guest is None or guest.can("download"))


# ----------------------------------------------------------------------- присланные файлы
@dataclass(slots=True)
class Job:
    user_id: int
    file_id: str
    name: str
    kind: str
    size: int
    photo: bool
    expires: float


_jobs: OrderedDict[int, Job] = OrderedDict()
_counter = 0
_recent: dict[int, deque[float]] = defaultdict(deque)


def _put(job: Job) -> int:
    global _counter
    now = time.time()
    for ref in [r for r, j in _jobs.items() if j.expires < now]:
        del _jobs[ref]
    while len(_jobs) >= MAX_JOBS:
        _jobs.popitem(last=False)
    _counter = _counter % 900_000 + 1
    _jobs[_counter] = job
    return _counter


def _get(ref: int, user_id: int) -> Job | None:
    job = _jobs.get(ref)
    return job if job is not None and job.user_id == user_id and job.expires >= time.time() else None


def over_quota(user_id: int, guest: Person | None) -> bool:
    """Лимит только для гостей; учитываем попытку сразу."""
    if guest is None:
        return False
    now = time.time()
    hits = _recent[user_id]
    while hits and hits[0] < now - 3600:
        hits.popleft()
    if len(hits) >= GUEST_PER_HOUR:
        return True
    hits.append(now)
    if len(_recent) > 500:
        _recent.clear()
    return False


def describe(message: Message) -> tuple[str, str, int, bool] | None:
    """(имя, MIME, размер, это фото) присланного файла или None, если там нет файла."""
    if message.document:
        d = message.document
        return d.file_name or "file", d.mime_type or "", d.file_size or 0, False
    if message.photo:
        p = message.photo[-1]
        return "photo.jpg", "image/jpeg", p.file_size or 0, True
    if message.video:
        v = message.video
        return v.file_name or "video.mp4", v.mime_type or "video/mp4", v.file_size or 0, False
    if message.audio:
        a = message.audio
        return a.file_name or f"{a.title or 'audio'}.mp3", a.mime_type or "audio/mpeg", a.file_size or 0, False
    return None


def file_id_of(message: Message) -> str:
    if message.document:
        return message.document.file_id
    if message.photo:
        return message.photo[-1].file_id
    if message.video:
        return message.video.file_id
    assert message.audio is not None
    return message.audio.file_id


def _is_archive(message: Message) -> bool:
    name = (message.document.file_name or "").lower() if message.document else ""
    return name.endswith((".tar.gz", ".tgz"))  # это обновление бота — им занимается раздел «Обновление»


def _is_settings(message: Message) -> bool:
    from homebot.services.transfer import is_settings_file  # 📦 перенос настроек — это раздел «Бэкап»

    return bool(message.document and is_settings_file(message.document.file_name))


def _is_file(message: Message) -> bool:
    return bool(
        (message.document or message.photo or message.video or message.audio)
        and not _is_archive(message)
        and not _is_settings(message)
    )


# ----------------------------------------------------------------------- меню и команда
async def _menu_view(guest: Person | None, converter: Converter) -> tuple[str, Any]:
    caps = converter.refresh()
    text = render_tools_menu(caps, admin=guest is None, download=can_download(guest, converter))
    markup = kb.tools_menu_kb(merge=caps.pillow and caps.pypdf, download=can_download(guest, converter))
    return text, markup


@router.message(Command("tools"))
async def cmd_tools(message: Message, state: FSMContext, converter: Converter, guest: Person | None = None) -> None:
    await state.clear()
    text, markup = await _menu_view(guest, converter)
    await message.answer(text, reply_markup=markup)


@router.callback_query(MenuCb.filter(F.action == MenuAction.TOOLS))
async def cb_tools(call: CallbackQuery, state: FSMContext, converter: Converter, guest: Person | None = None) -> None:
    await call.answer()
    await state.clear()
    text, markup = await _menu_view(guest, converter)
    await edit_or_send(call, text, markup)


@router.callback_query(ToolCb.filter(F.act == ToolAction.CANCEL))
async def cb_cancel(call: CallbackQuery, state: FSMContext, converter: Converter, guest: Person | None = None) -> None:
    await call.answer("Отменено")
    await state.clear()
    text, markup = await _menu_view(guest, converter)
    await edit_or_send(call, text, markup)


# ----------------------------------------------------------------------- один файл → операции
@router.message(StateFilter(None), _is_file)
async def on_file(message: Message, converter: Converter, guest: Person | None = None) -> None:
    info = describe(message)
    assert info is not None and message.from_user is not None
    name, mime, size, photo = info
    kind = kind_of(name, mime)
    if kind is None:
        await message.answer(UNKNOWN_FILE)
        return
    if size > MAX_INPUT:
        await message.answer(TOO_BIG)
        return
    caps = converter.refresh()
    ops = ops_for(kind, caps, name)
    if not ops:
        await message.answer(render_ops_prompt(kind, name, size, photo=photo, none=True))
        return
    ref = _put(Job(message.from_user.id, file_id_of(message), name, kind, size, photo, time.time() + JOB_TTL))
    await message.answer(render_ops_prompt(kind, name, size, photo=photo), reply_markup=kb.tools_ops_kb(ref, ops))


async def _send(message: Message, result: Result) -> None:
    file = FSInputFile(result.path, filename=result.path.name)
    caption = esc(result.caption)[:900] or None
    try:
        if result.kind == "audio":
            await message.answer_audio(file, caption=caption, title=result.title or None)
        elif result.kind == "voice":
            await message.answer_voice(file, caption=caption)
        elif result.kind == "video":
            await message.answer_video(file, caption=caption, supports_streaming=True)
        elif result.kind == "animation":
            await message.answer_animation(file, caption=caption)
        else:
            await message.answer_document(file, caption=caption)
    except TelegramEntityTooLarge as exc:
        raise ConvertError("результат больше 50 МБ — Telegram такой не принимает от бота") from exc


async def _download_input(bot: Bot, job: Job, dest: Path) -> Path:
    path = dest / "input"
    try:
        await bot.download(job.file_id, destination=path)
    except TelegramAPIError as exc:
        log.info("tools: download failed: %s", exc)
        raise ConvertError("не удалось скачать файл у Telegram — пришлите его ещё раз") from exc
    return path


@router.callback_query(ToolCb.filter(F.act == ToolAction.RUN))
async def cb_run(call: CallbackQuery, callback_data: ToolCb, bot: Bot, converter: Converter, guest: Person | None = None) -> None:
    message = call.message
    job = _get(callback_data.ref, call.from_user.id)
    if job is None or not isinstance(message, Message):
        await call.answer(EXPIRED, show_alert=True)
        return
    ops = ops_for(job.kind, converter.refresh(), job.name)
    if not 0 <= callback_data.idx < len(ops):
        await call.answer("Кнопка устарела — пришлите файл заново", show_alert=True)
        return
    if over_quota(call.from_user.id, guest):
        await call.answer(QUOTA, show_alert=True)
        return
    op = ops[callback_data.idx]
    await call.answer()
    head = render_ops_prompt(job.kind, job.name, job.size, photo=False)
    first = head.split("\n\n")[0]
    await edit_or_send(call, f"{first}\n\n{BUSY}" if converter.busy else f"{first}\n\n⏳ {esc(op.label)}…")
    error = ""
    async with converter.lock():
        await edit_or_send(call, f"{first}\n\n⏳ {esc(op.label)}…")
        try:
            with tempfile.TemporaryDirectory(prefix="tools-") as tmp:
                work = Path(tmp)
                src = await _download_input(bot, job, work)
                results = await converter.run(op.key, src, job.name, work / "out")
                for result in results:
                    await _send(message, result)
        except ConvertError as exc:
            error = str(exc)
        except Exception:
            log.exception("tools: %s failed", op.key)
            error = "что-то пошло не так, подробности в журнале"
    if error:
        await edit_or_send(call, f"{first}\n\n⚠️ {esc(error[:300])}", kb.tools_ops_kb(callback_data.ref, ops))
    else:
        await edit_or_send(call, f"{first}\n\n✅ Готово. Что ещё сделать с этим файлом?", kb.tools_ops_kb(callback_data.ref, ops))


# ----------------------------------------------------------------------- сборка PDF
@router.callback_query(ToolCb.filter(F.act == ToolAction.MERGE))
async def cb_merge(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(ToolsStates.merge)
    await state.update_data(files=[], total=0)
    await edit_or_send(call, MERGE_HELP, kb.tools_collect_kb(0))


def _mergeable(message: Message) -> bool:
    info = describe(message)
    return _is_file(message) and info is not None and kind_of(info[0], info[1]) in ("image", "pdf")


@router.message(ToolsStates.merge, _mergeable)
async def on_merge_file(message: Message, state: FSMContext) -> None:
    info = describe(message)
    assert info is not None
    name, _mime, size, _photo = info
    data = await state.get_data()
    files: list[list[Any]] = list(data.get("files", []))
    total = int(data.get("total", 0)) + size
    if len(files) >= MAX_MERGE_FILES:
        await message.answer(
            f"Уже {MAX_MERGE_FILES} файлов — это максимум. Нажмите «Собрать».", reply_markup=kb.tools_collect_kb(len(files))
        )
        return
    if size > MAX_INPUT or total > MAX_MERGE_BYTES:
        await message.answer(
            "Слишком много данных для одного PDF — соберите то, что уже есть, а остальное отдельным файлом.",
            reply_markup=kb.tools_collect_kb(len(files)),
        )
        return
    files.append([file_id_of(message), name])
    await state.update_data(files=files, total=total)
    await message.answer(
        f"➕ Добавлено: {len(files)} · {size_text(total)}\nПрисылайте ещё или нажмите «Собрать».",
        reply_markup=kb.tools_collect_kb(len(files)),
    )


def _not_command(message: Message) -> bool:
    return not (message.text or "").startswith("/")


@router.message(ToolsStates.merge, _not_command)
async def on_merge_other(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await message.answer("Жду фото или PDF. Другое сюда не добавить.", reply_markup=kb.tools_collect_kb(len(data.get("files", []))))


@router.callback_query(ToolCb.filter(F.act == ToolAction.DONE), ToolsStates.merge)
async def cb_merge_done(call: CallbackQuery, state: FSMContext, bot: Bot, converter: Converter, guest: Person | None = None) -> None:
    message = call.message
    data = await state.get_data()
    files: list[list[Any]] = list(data.get("files", []))
    if not files or not isinstance(message, Message):
        await call.answer("Сначала пришлите хотя бы один файл", show_alert=True)
        return
    if over_quota(call.from_user.id, guest):
        await call.answer(QUOTA, show_alert=True)
        return
    await call.answer()
    await edit_or_send(call, BUSY if converter.busy else f"⏳ Собираю PDF из {len(files)} файлов…")
    error = ""
    async with converter.lock():
        try:
            with tempfile.TemporaryDirectory(prefix="tools-") as tmp:
                work = Path(tmp)
                paths: list[Path] = []
                for i, (file_id, name) in enumerate(files):
                    job = Job(call.from_user.id, str(file_id), str(name), "image", 0, False, 0)
                    folder = work / f"in{i}"
                    folder.mkdir()
                    path = await _download_input(bot, job, folder)
                    suffix = Path(str(name)).suffix.lower() or ".jpg"
                    path = path.rename(path.with_name(f"part{i}{suffix}"))
                    paths.append(path)
                result = await converter.merge(paths, work / "out")
                await _send(message, result)
        except ConvertError as exc:
            error = str(exc)
        except Exception:
            log.exception("tools: merge failed")
            error = "что-то пошло не так, подробности в журнале"
    if error:
        await edit_or_send(call, f"⚠️ {esc(error[:300])}", kb.tools_collect_kb(len(files)))
        return
    await state.clear()
    await edit_or_send(call, "✅ PDF собран и отправлен.", kb.tools_menu_kb(merge=True, download=can_download(guest, converter)))


# ----------------------------------------------------------------------- скачивание по ссылке
@router.callback_query(ToolCb.filter(F.act == ToolAction.LINK))
async def cb_link(call: CallbackQuery, state: FSMContext, converter: Converter, guest: Person | None = None) -> None:
    if not can_download(guest, converter):
        await call.answer("Скачивание по ссылке вам не включено", show_alert=True)
        return
    await call.answer()
    await state.set_state(ToolsStates.link)
    await edit_or_send(call, LINK_HELP, kb.tools_collect_kb(0))


@router.message(ToolsStates.link, F.text, ~F.text.startswith("/"))
async def on_link(message: Message, state: FSMContext, converter: Converter, guest: Person | None = None) -> None:
    if not can_download(guest, converter):
        await state.clear()
        await message.answer("Скачивание по ссылке вам не включено.")
        return
    match = URL_RE.search(message.text or "")
    if match is None:
        await message.answer("Не вижу ссылки. Пришлите адрес, который начинается с https://", reply_markup=kb.tools_collect_kb(0))
        return
    try:
        url = await public_http_url(match.group(0).rstrip(".,)"))
    except ValueError as exc:
        await message.answer(f"⚠️ {esc(str(exc))}", reply_markup=kb.tools_collect_kb(0))
        return
    await state.update_data(url=url)
    await message.answer("Что скачать?", reply_markup=kb.tools_link_kb(ffmpeg=converter.caps.ffmpeg))


@router.callback_query(ToolCb.filter(F.act == ToolAction.GET), ToolsStates.link)
async def cb_get(
    call: CallbackQuery, callback_data: ToolCb, state: FSMContext, converter: Converter, guest: Person | None = None
) -> None:
    message = call.message
    url = (await state.get_data()).get("url")
    if not url or not isinstance(message, Message):
        await call.answer(EXPIRED, show_alert=True)
        return
    if not can_download(guest, converter):
        await call.answer("Скачивание по ссылке вам не включено", show_alert=True)
        return
    if over_quota(call.from_user.id, guest):
        await call.answer(QUOTA, show_alert=True)
        return
    await call.answer()
    mode = "audio" if callback_data.idx == 0 else "video"
    await edit_or_send(call, BUSY if converter.busy else "⏳ Скачиваю… (может занять пару минут)")
    error = ""
    async with converter.lock():
        try:
            with tempfile.TemporaryDirectory(prefix="tools-") as tmp:
                result = await converter.download(str(url), mode, Path(tmp))
                await _send(message, result)
        except ConvertError as exc:
            error = str(exc)
        except Exception:
            log.exception("tools: download failed")
            error = "что-то пошло не так, подробности в журнале"
    if error:
        await edit_or_send(call, f"⚠️ {esc(error[:300])}", kb.tools_collect_kb(0))
        return
    await state.clear()
    await edit_or_send(call, "✅ Готово.", kb.tools_menu_kb(merge=True, download=True))
