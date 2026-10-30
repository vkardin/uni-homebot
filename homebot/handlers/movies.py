"""🎬 Кино: поиск и подбор фильмов и сериалов, закачка через Radarr/Sonarr, запуск на ТВ через Jellyfin.

Всё происходит в одном сообщении: список → карточка с постером → пульт. Новых сообщений бот
не плодит; уведомления «скачано» присылает фоновая задача (jobs/movies_watch.py).
"""

from __future__ import annotations

import asyncio
import html
import logging
import random
import time
from collections.abc import Awaitable, Sequence
from typing import TypeVar

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.chat_action import ChatActionSender

from homebot.config import Settings
from homebot.jobs.movies_watch import KEY_REQUESTED
from homebot.services.apps import Apps
from homebot.services.apps_http import AppError
from homebot.services.jellyfin import LibraryMovie, Screen, find_by_tmdb, pick_screen
from homebot.services.movie_picks import KIND_MOVIE, KIND_SERIES, MOODS, is_cyrillic, recommend, translate_title
from homebot.services.ollama import OllamaError, OllamaService
from homebot.services.radarr import Download, Film
from homebot.services.sonarr import MONITOR_ALL, MONITOR_LATEST, Show, ShowDownload
from homebot.services.state import StateStore
from homebot.states import MovieStates
from homebot.ui import keyboards as kb
from homebot.ui.callbacks import MenuAction, MenuCb, MovieAction, MovieCb
from homebot.ui.movie_texts import (
    MOOD_PROMPT,
    MOVIES_OFF,
    SEARCH_PROMPT,
    SEARCHING,
    THINKING,
    render_film,
    render_film_list,
    render_library_picks,
    render_movies_home,
    render_show,
)
from homebot.ui.texts import render_ollama_error
from homebot.utils.telegram import edit_or_send, link_preview

log = logging.getLogger(__name__)
router = Router(name="movies")

T = TypeVar("T")
Entry = tuple[Film | Show, str, Download | ShowDownload | None]


# ------------------------------------------------------------------ помощники
async def _safe(coro: Awaitable[T] | None, default: T, notes: list[str] | None = None, label: str = "") -> T:
    """Ошибка одного сервиса не должна ломать весь экран: показываем её строкой внизу."""
    if coro is None:
        return default
    try:
        return await coro
    except AppError as exc:
        if notes is not None:
            notes.append(f"{label}: {exc}")
        return default


async def _show(target: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None, preview: str | None = None) -> None:
    """Обновить экран: у кнопки — её сообщение, у команды — наш «⏳»-заполнитель."""
    if isinstance(target, CallbackQuery):
        await edit_or_send(target, text, markup, preview=preview)
        return
    try:
        await target.edit_text(text, reply_markup=markup, link_preview_options=link_preview(preview))
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc).lower():
            await target.answer(text, reply_markup=markup, link_preview_options=link_preview(preview))


def _connected(apps: Apps) -> bool:
    return apps.radarr is not None or apps.sonarr is not None


def _label(item: Film | Show, download: object | None = None) -> str:
    return f"{kb.film_icon(item, download is not None)} {item.label}"


async def _remember_request(store: StateStore, key: str) -> None:
    requested = dict(store.get(KEY_REQUESTED) or {})
    requested[key] = time.time()
    await store.set(KEY_REQUESTED, requested)


# ------------------------------------------------------------------ главный экран
async def _home(apps: Apps, settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    if not _connected(apps):
        return MOVIES_OFF, kb.back_kb()
    notes: list[str] = []
    radarr, sonarr, jellyfin = apps.radarr, apps.sonarr, apps.jellyfin
    m_queue, m_lib, s_queue, s_lib, screens = await asyncio.gather(
        _safe(radarr.queue() if radarr else None, {}, notes, "Radarr"),
        _safe(radarr.library() if radarr else None, None, notes, "Radarr"),
        _safe(sonarr.queue() if sonarr else None, {}, notes, "Sonarr"),
        _safe(sonarr.library() if sonarr else None, None, notes, "Sonarr"),
        _safe(jellyfin.screens() if jellyfin else None, [], notes, "Jellyfin"),
    )
    films = {f.radarr_id: f for f in m_lib or []}
    shows = {s.sonarr_id: s for s in s_lib or []}
    downloads: list[tuple[str, float, str, bool]] = []
    for movie_id, d in m_queue.items():
        film = films.get(movie_id)
        downloads.append((f"🎬 {film.label if film else d.title}", d.progress, d.timeleft, bool(d.problem)))
    for series_id, d in s_queue.items():
        show = shows.get(series_id)
        name = f"📺 {show.title if show else 'сериал'} ({d.episodes} сер.)"
        downloads.append((name, d.progress, d.timeleft, bool(d.problem)))
    parts = []
    if m_lib is not None:
        parts.append(f"фильмов — {sum(f.has_file for f in m_lib)}")
    if s_lib is not None:
        parts.append(f"сериалов — {sum(s.has_file for s in s_lib)}")
    screen = pick_screen(screens, settings.jellyfin_tv)
    text = render_movies_home(downloads, screen, ", ".join(parts), list(dict.fromkeys(notes)))
    return text, kb.movies_kb()


@router.message(Command("movies", "kino", "films"))
async def cmd_movies(message: Message, state: FSMContext, apps: Apps, settings: Settings) -> None:
    await state.clear()
    placeholder = await message.answer("⏳ Загружаю…")
    text, markup = await _home(apps, settings)
    await _show(placeholder, text, markup)


@router.callback_query(MenuCb.filter(F.action == MenuAction.MOVIES))
@router.callback_query(MovieCb.filter(F.action == MovieAction.HOME))
async def cb_movies(call: CallbackQuery, state: FSMContext, apps: Apps, settings: Settings) -> None:
    await call.answer()
    await state.clear()
    text, markup = await _home(apps, settings)
    await edit_or_send(call, text, markup)


# ------------------------------------------------------------------ списки
async def _entries(apps: Apps, items: Sequence[tuple[Film | Show, str]]) -> list[Entry]:
    """Подставляем версии из библиотеки (там «скачано/нет») и прогресс загрузки."""
    need_m = apps.radarr is not None and any(isinstance(i, Film) for i, _ in items)
    need_s = apps.sonarr is not None and any(isinstance(i, Show) for i, _ in items)
    m_lib, m_queue, s_lib, s_queue = await asyncio.gather(
        _safe(apps.radarr.library() if need_m and apps.radarr else None, []),
        _safe(apps.radarr.queue() if need_m and apps.radarr else None, {}),
        _safe(apps.sonarr.library() if need_s and apps.sonarr else None, []),
        _safe(apps.sonarr.queue() if need_s and apps.sonarr else None, {}),
    )
    films = {f.tmdb_id: f for f in m_lib}
    shows = {s.tvdb_id: s for s in s_lib}
    result: list[Entry] = []
    for item, why in items:
        if isinstance(item, Show):
            show = shows.get(item.tvdb_id, item)
            result.append((show, why, s_queue.get(show.sonarr_id) if show.in_sonarr else None))
        else:
            film = films.get(item.tmdb_id, item)
            result.append((film, why, m_queue.get(film.radarr_id) if film.in_radarr else None))
    return result


def _list_view(header: str, entries: list[Entry], again: MovieCb | None = None) -> tuple[str, InlineKeyboardMarkup]:
    buttons = [(kb.card_cb(item), _label(item, download)) for item, _, download in entries]
    return render_film_list(header, entries), kb.films_kb(buttons, again=again)


async def _search(apps: Apps, ollama: OllamaService, query: str) -> tuple[str, InlineKeyboardMarkup]:
    radarr, sonarr = apps.radarr, apps.sonarr

    async def both(term: str) -> tuple[list[Film], list[Show]]:
        notes: list[str] = []
        films, shows = await asyncio.gather(
            _safe(radarr.lookup(term) if radarr else None, [], notes, "Radarr"),
            _safe(sonarr.lookup(term) if sonarr else None, [], notes, "Sonarr"),
        )
        if notes and not films and not shows:
            raise AppError("; ".join(notes))
        return films, shows

    films, shows = await both(query)
    if not films and not shows and is_cyrillic(query):
        term = await translate_title(ollama, query)  # «Пацаны» → «The Boys 2019»
        if term:
            films, shows = await both(term)
    items: list[tuple[Film | Show, str]] = [(f, "") for f in films[:5]] + [(s, "") for s in shows[:5]]
    return _list_view(f"🔍 <b>{html.escape(query)}</b>", await _entries(apps, items))


@router.callback_query(MovieCb.filter(F.action == MovieAction.SEARCH))
async def cb_search(call: CallbackQuery, state: FSMContext, apps: Apps) -> None:
    if not _connected(apps):
        await call.answer("Radarr/Sonarr не подключены", show_alert=True)
        return
    await call.answer()
    await state.set_state(MovieStates.search)
    await edit_or_send(call, SEARCH_PROMPT, kb.movie_cancel_kb())


@router.message(Command("movie", "find"))
async def cmd_find(message: Message, command: CommandObject, state: FSMContext, apps: Apps, ollama: OllamaService) -> None:
    if not command.args:
        await state.set_state(MovieStates.search)
        await message.answer(SEARCH_PROMPT, reply_markup=kb.movie_cancel_kb())
        return
    await _run_search(message, command.args, state, apps, ollama)


@router.message(MovieStates.search, F.text, ~F.text.startswith("/"))
async def on_search_text(message: Message, state: FSMContext, apps: Apps, ollama: OllamaService) -> None:
    await _run_search(message, message.text or "", state, apps, ollama)


async def _run_search(message: Message, query: str, state: FSMContext, apps: Apps, ollama: OllamaService) -> None:
    await state.clear()
    query = " ".join(query.split())[:100]
    if not _connected(apps):
        await message.answer(MOVIES_OFF, reply_markup=kb.back_kb())
        return
    placeholder = await message.answer(SEARCHING)
    try:
        text, markup = await _search(apps, ollama, query)
    except AppError as exc:
        text, markup = f"🔍 Поиск не удался: {exc}", kb.movies_kb()
    await _show(placeholder, text, markup)


# ------------------------------------------------------------------ «что посмотреть?»
@router.callback_query(MovieCb.filter(F.action == MovieAction.MOODS))
async def cb_moods(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await edit_or_send(
        call, "🎲 <b>Что посмотреть?</b>\nВыберите настроение — UNI подберёт и проверит, что всё это реально есть.", kb.moods_kb()
    )


async def _recommend_view(
    target: Message | CallbackQuery, apps: Apps, ollama: OllamaService, bot: Bot, request_text: str, kind: str, again: MovieCb | None
) -> None:
    lookup = apps.sonarr if kind == KIND_SERIES else apps.radarr
    if lookup is None:
        await _show(
            target, "📺 Для сериалов нужен Sonarr — см. инструкцию в README." if kind == KIND_SERIES else MOVIES_OFF, kb.moods_kb()
        )
        return
    library = await _safe(lookup.library(), [])
    exclude = tuple(item.title for item in library)[:40]
    if isinstance(target, CallbackQuery):
        chat_id = target.message.chat.id if target.message else target.from_user.id
    else:
        chat_id = target.chat.id
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=chat_id):
            picks = await recommend(lookup, ollama, request_text, count=5, exclude=exclude, kind=kind)
    except OllamaError as exc:
        await _show(target, render_ollama_error(exc), kb.moods_kb())
        return
    except AppError as exc:
        await _show(target, f"🎲 Не получилось: {html.escape(str(exc))}", kb.moods_kb())
        return
    header = "🎲 <b>UNI советует</b>" + (f" · {html.escape(request_text[:40])}" if again is None else "")
    text, markup = _list_view(header, await _entries(apps, picks), again=again)
    await _show(target, text, markup)


@router.callback_query(MovieCb.filter(F.action == MovieAction.MOOD))
async def cb_mood(call: CallbackQuery, callback_data: MovieCb, apps: Apps, ollama: OllamaService, bot: Bot) -> None:
    mood = MOODS.get(callback_data.arg)
    if mood is None:
        await call.answer("Кнопка устарела", show_alert=True)
        return
    await call.answer("🤔 Подбираю…")
    label, request_text, kind = mood
    await edit_or_send(call, f"{label}\n{THINKING}")
    await _recommend_view(call, apps, ollama, bot, request_text, kind, again=callback_data)


@router.callback_query(MovieCb.filter(F.action == MovieAction.CUSTOM))
async def cb_custom(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.set_state(MovieStates.mood)
    await edit_or_send(call, MOOD_PROMPT, kb.movie_cancel_kb())


@router.message(MovieStates.mood, F.text, ~F.text.startswith("/"))
async def on_mood_text(message: Message, state: FSMContext, apps: Apps, ollama: OllamaService, bot: Bot) -> None:
    await state.clear()
    request_text = " ".join((message.text or "").split())[:300]
    kind = KIND_SERIES if "сериал" in request_text.lower() else KIND_MOVIE
    placeholder = await message.answer(THINKING)
    await _recommend_view(placeholder, apps, ollama, bot, request_text, kind, again=None)


@router.message(Command("watch"))
async def cmd_watch(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🎲 <b>Что посмотреть?</b>\nВыберите настроение:", reply_markup=kb.moods_kb())


@router.callback_query(MovieCb.filter(F.action == MovieAction.TRENDING))
async def cb_trending(call: CallbackQuery, apps: Apps) -> None:
    if apps.radarr is None:
        await call.answer("Нужен Radarr", show_alert=True)
        return
    await call.answer()
    await edit_or_send(call, "🔥 Смотрю, что сейчас популярно…")
    try:
        films = await apps.radarr.discover()
    except AppError as exc:
        await edit_or_send(call, f"🔥 Не удалось получить подборку: {html.escape(str(exc))}", kb.movies_kb())
        return
    text, markup = _list_view("🔥 <b>Популярное и рекомендации</b>", await _entries(apps, [(f, "") for f in films[:10]]))
    await edit_or_send(call, text, markup)


@router.callback_query(MovieCb.filter(F.action == MovieAction.LIBRARY))
async def cb_library(call: CallbackQuery, apps: Apps) -> None:
    if apps.jellyfin is None:
        await call.answer("Нужен Jellyfin (JELLYFIN_API_KEY)", show_alert=True)
        return
    await call.answer()
    try:
        movies, series = await asyncio.gather(apps.jellyfin.movies(), apps.jellyfin.movies("Series"))
    except AppError as exc:
        await edit_or_send(call, f"🎞 Jellyfin: {html.escape(str(exc))}", kb.movies_kb())
        return
    resume = [m for m in movies if m.resume_ticks and m.tmdb_id]
    fresh = [m for m in movies if not m.played and not m.resume_ticks and m.tmdb_id]
    shows = [s for s in series if not s.played and s.tvdb_id]
    picks = resume[:3] + random.sample(fresh, min(len(fresh), 6 - min(len(resume), 3))) + random.sample(shows, min(len(shows), 3))
    buttons = [
        (
            kb.card_cb(Show(m.tvdb_id, m.title)) if m.kind == "Series" else kb.card_cb(Film(m.tmdb_id, m.title)),
            f"{'▶️' if m.resume_ticks else ('📺' if m.kind == 'Series' else '🎬')} {m.title}" + (f" ({m.year})" if m.year else ""),
        )
        for m in picks
    ]
    await edit_or_send(call, render_library_picks(picks), kb.films_kb(buttons, again=MovieCb(action=MovieAction.LIBRARY)))


# ------------------------------------------------------------------ карточки
async def _jellyfin_item(apps: Apps, item: Film | Show) -> LibraryMovie | None:
    if apps.jellyfin is None or not item.has_file:
        return None
    if isinstance(item, Show):
        series = await _safe(apps.jellyfin.movies("Series"), [])
        return find_by_tmdb(series, 0, item.title, item.year, tvdb_id=item.tvdb_id)
    movies = await _safe(apps.jellyfin.movies(), [])
    return find_by_tmdb(movies, item.tmdb_id, item.title, item.year)


async def _film_card(apps: Apps, tmdb: int) -> tuple[str, InlineKeyboardMarkup, str | None] | None:
    assert apps.radarr is not None
    film = await apps.radarr.by_tmdb(tmdb)
    if film is None:
        return None
    queue = await _safe(apps.radarr.queue(), {}) if film.in_radarr and not film.has_file else {}
    download = queue.get(film.radarr_id)
    lib = await _jellyfin_item(apps, film)
    text = render_film(film, download, lib, jellyfin=apps.jellyfin is not None)
    markup = kb.film_kb(film, playable=lib is not None, resume=bool(lib and lib.resume_ticks), downloading=download is not None)
    return text, markup, film.poster or None


async def _show_card(apps: Apps, tvdb: int) -> tuple[str, InlineKeyboardMarkup, str | None] | None:
    assert apps.sonarr is not None
    show = await apps.sonarr.by_tvdb(tvdb)
    if show is None:
        return None
    queue = await _safe(apps.sonarr.queue(), {}) if show.in_sonarr else {}
    download = queue.get(show.sonarr_id)
    lib = await _jellyfin_item(apps, show)
    text = render_show(show, download, lib, jellyfin=apps.jellyfin is not None)
    return text, kb.show_kb(show, playable=lib is not None, downloading=download is not None), show.poster or None


async def _open_card(call: CallbackQuery, apps: Apps, series: bool, ref: int, toast: str = "") -> None:
    try:
        card = await (_show_card(apps, ref) if series else _film_card(apps, ref))
    except AppError as exc:
        await call.answer(f"Ошибка: {exc}"[:200], show_alert=True)
        return
    if card is None:
        await call.answer("Не нашёл — попробуйте поиск заново", show_alert=True)
        return
    await call.answer(toast)
    text, markup, poster = card
    await edit_or_send(call, text, markup, preview=poster)


@router.callback_query(MovieCb.filter(F.action == MovieAction.CARD))
async def cb_card(call: CallbackQuery, callback_data: MovieCb, apps: Apps) -> None:
    if apps.radarr is None:
        await call.answer("Radarr не подключён", show_alert=True)
        return
    await _open_card(call, apps, False, callback_data.ref)


@router.callback_query(MovieCb.filter(F.action == MovieAction.S_CARD))
async def cb_show_card(call: CallbackQuery, callback_data: MovieCb, apps: Apps) -> None:
    if apps.sonarr is None:
        await call.answer("Sonarr не подключён", show_alert=True)
        return
    await _open_card(call, apps, True, callback_data.ref)


# ------------------------------------------------------------------ скачать
@router.callback_query(MovieCb.filter(F.action == MovieAction.ADD))
async def cb_add(call: CallbackQuery, callback_data: MovieCb, apps: Apps, store: StateStore) -> None:
    if apps.radarr is None:
        await call.answer("Radarr не подключён", show_alert=True)
        return
    try:
        film = await apps.radarr.add(callback_data.ref)
    except AppError as exc:
        await call.answer(f"Не добавилось: {exc}"[:200], show_alert=True)
        return
    await _remember_request(store, f"m:{film.radarr_id}")
    await _open_card(call, apps, False, callback_data.ref, toast="⬇️ Добавил! Сообщу, когда скачается 🍿")


@router.callback_query(MovieCb.filter(F.action == MovieAction.S_ADD))
async def cb_show_add(call: CallbackQuery, callback_data: MovieCb, apps: Apps, store: StateStore) -> None:
    if apps.sonarr is None:
        await call.answer("Sonarr не подключён", show_alert=True)
        return
    monitor = MONITOR_LATEST if callback_data.arg == "last" else MONITOR_ALL
    try:
        show = await apps.sonarr.add(callback_data.ref, monitor=monitor)
    except AppError as exc:
        await call.answer(f"Не добавилось: {exc}"[:200], show_alert=True)
        return
    await _remember_request(store, f"s:{show.sonarr_id}")
    what = "последний сезон" if monitor == MONITOR_LATEST else "все сезоны"
    await _open_card(call, apps, True, callback_data.ref, toast=f"⬇️ Качаю {what}! Сообщу о новых сериях 🍿")


@router.callback_query(MovieCb.filter(F.action.in_({MovieAction.RESEARCH, MovieAction.S_RESEARCH})))
async def cb_research(call: CallbackQuery, callback_data: MovieCb, apps: Apps, store: StateStore) -> None:
    series = callback_data.action == MovieAction.S_RESEARCH
    try:
        if series and apps.sonarr is not None:
            show = await apps.sonarr.by_tvdb(callback_data.ref)
            if show is None or not show.in_sonarr:
                raise AppError("сериала нет в Sonarr")
            await apps.sonarr.search(show.sonarr_id)
            await _remember_request(store, f"s:{show.sonarr_id}")
        elif not series and apps.radarr is not None:
            film = await apps.radarr.by_tmdb(callback_data.ref)
            if film is None or not film.in_radarr:
                raise AppError("фильма нет в Radarr")
            await apps.radarr.search(film.radarr_id)
            await _remember_request(store, f"m:{film.radarr_id}")
        else:
            raise AppError("не подключено")
    except AppError as exc:
        await call.answer(f"Ошибка: {exc}"[:200], show_alert=True)
        return
    await _open_card(call, apps, series, callback_data.ref, toast="🔎 Ищу раздачу заново")


# ------------------------------------------------------------------ телевизор
async def _screen(apps: Apps, settings: Settings) -> Screen | None:
    assert apps.jellyfin is not None
    return pick_screen(await apps.jellyfin.screens(), settings.jellyfin_tv)


async def _play(
    call: CallbackQuery, apps: Apps, settings: Settings, item: LibraryMovie | None, start_ticks: int, missing: str
) -> None:
    assert apps.jellyfin is not None
    if item is None:
        await _safe(apps.jellyfin.refresh_library(), None)
        await call.answer(missing, show_alert=True)
        return
    screen = await _screen(apps, settings)
    if screen is None:
        await call.answer("📺 Телевизор не на связи. Откройте Jellyfin на Mi Stick и нажмите ещё раз.", show_alert=True)
        return
    await apps.jellyfin.play(screen.id, item.id, start_ticks)
    await call.answer(f"▶️ Запускаю на {screen.device or screen.client}")


@router.callback_query(MovieCb.filter(F.action == MovieAction.TV))
async def cb_tv(call: CallbackQuery, callback_data: MovieCb, apps: Apps, settings: Settings) -> None:
    if apps.jellyfin is None:
        await call.answer("Нужен Jellyfin (JELLYFIN_API_KEY в .env)", show_alert=True)
        return
    try:
        movies = await apps.jellyfin.movies()
        film = await apps.radarr.by_tmdb(callback_data.ref) if apps.radarr else None
        item = find_by_tmdb(movies, callback_data.ref, film.title if film else "", film.year if film else None)
        start = item.resume_ticks if item is not None and callback_data.arg == "r" else 0
        await _play(
            call, apps, settings, item, start, "🔄 Jellyfin ещё не видит этот фильм — обновил медиатеку, нажмите через минуту."
        )
    except AppError as exc:
        await call.answer(f"Jellyfin: {exc}"[:200], show_alert=True)


@router.callback_query(MovieCb.filter(F.action == MovieAction.S_TV))
async def cb_show_tv(call: CallbackQuery, callback_data: MovieCb, apps: Apps, settings: Settings) -> None:
    if apps.jellyfin is None:
        await call.answer("Нужен Jellyfin (JELLYFIN_API_KEY в .env)", show_alert=True)
        return
    try:
        series = await apps.jellyfin.movies("Series")
        show = await apps.sonarr.by_tvdb(callback_data.ref) if apps.sonarr else None
        found = find_by_tmdb(series, 0, show.title if show else "", show.year if show else None, tvdb_id=callback_data.ref)
        episode = await apps.jellyfin.next_episode(found.id) if found else None
        if found is not None and episode is None:
            await call.answer("🎉 Все серии уже просмотрены", show_alert=True)
            return
        missing = "🔄 Jellyfin ещё не видит этот сериал — обновил медиатеку, нажмите через минуту."
        await _play(call, apps, settings, episode, episode.resume_ticks if episode else 0, missing)
    except AppError as exc:
        await call.answer(f"Jellyfin: {exc}"[:200], show_alert=True)
