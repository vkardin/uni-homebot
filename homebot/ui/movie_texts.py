"""Тексты раздела «🎬 Кино»."""

from __future__ import annotations

import html
from collections.abc import Sequence

from homebot.services.jellyfin import LibraryMovie, Screen
from homebot.services.radarr import Download, Film
from homebot.services.sonarr import Show, ShowDownload
from homebot.ui.style import DIV
from homebot.utils.formatting import bar, truncate

esc = html.escape

MOVIES_OFF = (
    "🎬 <b>Кино</b>\n"
    f"{DIV}\n"
    "Radarr и Sonarr не подключены. Обычно ключи подставляются сами при обновлении бота:\n"
    "<code>sudo bash /opt/homebot/deploy/update.sh</code>\n\n"
    "Если нет — впишите <code>RADARR_API_KEY</code> / <code>SONARR_API_KEY</code> "
    "(Settings → General → API Key) в <code>/opt/homebot/.env</code> и запустите скрипт снова."
)
SEARCH_PROMPT = (
    "🔍 <b>Что ищем?</b>\nНапишите название фильма или сериала — можно по-русски, можно с годом: <i>Дюна 2021</i>, <i>Пацаны</i>"
)
MOOD_PROMPT = (
    "✍️ <b>Что хочется посмотреть?</b>\n"
    "Опишите своими словами, например: <i>что-то как «Интерстеллар», но полегче</i> "
    "или <i>смешное на вечер с девушкой</i>"
)
THINKING = "🤔 UNI подбирает фильмы… Локальной модели нужно до минуты."
SEARCHING = "🔍 Ищу…"

_GENRES = {
    "Action": "боевик",
    "Adventure": "приключения",
    "Animation": "мультфильм",
    "Comedy": "комедия",
    "Crime": "криминал",
    "Documentary": "документальный",
    "Drama": "драма",
    "Family": "семейный",
    "Fantasy": "фэнтези",
    "History": "история",
    "Horror": "ужасы",
    "Music": "музыка",
    "Mystery": "детектив",
    "Romance": "мелодрама",
    "Science Fiction": "фантастика",
    "Thriller": "триллер",
    "War": "военный",
    "Western": "вестерн",
    "TV Movie": "телефильм",
}


def runtime_label(minutes: int) -> str:
    if minutes <= 0:
        return ""
    hours, mins = divmod(minutes, 60)
    return f"{hours} ч {mins:02d} мин" if hours else f"{mins} мин"


def _clock(seconds: int) -> str:
    hours, rest = divmod(max(0, seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def genres_label(genres: Sequence[str]) -> str:
    return ", ".join(_GENRES.get(g, g.lower()) for g in genres)


def _meta(film: Film | Show) -> str:
    if isinstance(film, Show):
        seasons = f"{film.seasons} сез." if film.seasons else ""
        ended = {"ended": "завершён", "continuing": "выходит", "upcoming": "скоро"}.get(film.status, "")
        parts = [str(film.year) if film.year else "", f"⭐ {film.rating:.1f}" if film.rating else "", seasons, ended]
    else:
        parts = [str(film.year) if film.year else "", f"⭐ {film.rating:.1f}" if film.rating else "", runtime_label(film.runtime)]
    return " · ".join(p for p in parts if p)


def show_status(show: Show, download: ShowDownload | None) -> str:
    if download is not None:
        if download.problem and download.progress >= 100:
            return f"⚠️ Скачано, но не импортировано: {esc(truncate(download.problem, 120))}"
        left = f" · осталось {download.timeleft}" if download.timeleft else ""
        return f"⏬ Качается серий: {download.episodes} · {bar(download.progress, 8)} {download.progress:.0f}%{left}"
    if not show.in_sonarr:
        return ""
    if show.complete:
        return f"✅ Всё скачано: {show.episodes_have} сер."
    if show.has_file:
        return f"🟡 Скачано {show.episodes_have} из {show.episodes_total} сер. — остальные Sonarr ищет"
    return "⏳ В очереди Sonarr — ищет раздачи"


def film_status(film: Film, download: Download | None) -> str:
    if film.has_file:
        return "✅ В медиатеке"
    if download is not None:
        if download.problem and download.state in ("importBlocked", "importPending", "failedPending", "warning"):
            return f"⚠️ Скачано, но не импортировано: {esc(truncate(download.problem, 120))}"
        left = f" · осталось {download.timeleft}" if download.timeleft else ""
        return f"⏬ Качается: {bar(download.progress, 8)} {download.progress:.0f}%{left}"
    if film.in_radarr:
        if film.status in ("announced", "inCinemas"):
            return "🕰 Ещё не вышел в цифре — Radarr скачает, как только появится"
        return "⏳ В очереди Radarr — ищет раздачу"
    return ""


def render_movies_home(
    downloads: Sequence[tuple[str, float, str, bool]],
    screen: Screen | None,
    library: str,
    notes: Sequence[str] = (),
) -> str:
    lines = ["🎬 <b>Кино</b>", DIV]
    if screen is not None and screen.playing:
        state = "⏸" if screen.paused else "▶️"
        lines.append(f"📺 {esc(screen.device or screen.client)}: {state} {esc(truncate(screen.title, 50))}")
    elif screen is not None:
        lines.append(f"📺 {esc(screen.device or screen.client)} на связи — можно запускать")
    else:
        lines.append("📺 Телевизор не на связи — откройте Jellyfin на стике")
    if library:
        lines.append(f"🎞 В медиатеке: {library}")
    if downloads:
        lines += ["", "<b>Качается:</b>"]
        for name, progress, timeleft, problem in downloads[:10]:
            left = f" · {timeleft}" if timeleft else ""
            warn = " ⚠️" if problem else ""
            lines.append(f"⏬ {esc(truncate(name, 45))} — {progress:.0f}%{left}{warn}")
    lines += ["", "Найдите фильм или сериал по названию — или спросите UNI, что посмотреть 🍿"]
    lines += [f"⚠️ {esc(n)}" for n in notes]
    return "\n".join(lines)


def render_film_list(header: str, entries: Sequence[tuple[Film | Show, str, Download | ShowDownload | None]]) -> str:
    lines = [header, DIV]
    if not entries:
        lines.append("Ничего не нашлось 😕 Попробуйте другое название или английский вариант.")
        return "\n".join(lines)
    for n, (film, why, download) in enumerate(entries, 1):
        meta = _meta(film)
        icon = "📺" if isinstance(film, Show) else "🎬"
        lines.append(f"{icon} <b>{n}. {esc(film.title)}</b>" + (f" · {meta}" if meta else ""))
        if isinstance(film, Film) and film.original_title and film.original_title != film.title:
            lines.append(f"   <i>{esc(truncate(film.original_title, 60))}</i>")
        detail = why or truncate(film.overview, 110)
        if detail:
            lines.append(f"   {esc(truncate(detail, 150))}")
        if isinstance(film, Show):
            status = show_status(film, download if isinstance(download, ShowDownload) else None)
        else:
            status = film_status(film, download if isinstance(download, Download) else None)
        if status:
            lines.append(f"   {status}")
    lines += ["", "Нажмите на название — откроется карточка."]
    return "\n".join(lines)


def render_library_picks(movies: Sequence[LibraryMovie]) -> str:
    lines = ["🎞 <b>Из скачанного — ещё не смотрели</b>", DIV]
    if not movies:
        lines.append("Всё скачанное уже просмотрено 🎉 Пора найти что-то новое.")
    for movie in movies:
        year = f" ({movie.year})" if movie.year else ""
        resume = f" · ▶️ остановились на {movie.resume_percent:.0f}%" if movie.resume_ticks else ""
        icon = "📺" if movie.kind == "Series" else "🎬"
        lines.append(f"{icon} {esc(movie.title)}{year}{resume}")
    return "\n".join(lines)


def render_film(film: Film, download: Download | None, lib: LibraryMovie | None, *, jellyfin: bool) -> str:
    lines = [f"🎬 <b>{esc(film.title)}</b>"]
    if film.original_title and film.original_title != film.title:
        lines.append(f"<i>{esc(film.original_title)}</i>")
    meta = _meta(film)
    if meta:
        lines.append(meta)
    if film.genres:
        lines.append(f"🎭 {esc(genres_label(film.genres))}")
    lines.append(DIV)
    if film.overview:
        lines.append(esc(truncate(film.overview, 700)))
        lines.append("")
    status = film_status(film, download)
    if status:
        lines.append(status)
    if film.has_file:
        if lib is not None and lib.resume_ticks:
            lines.append(f"▶️ Остановились на {lib.resume_percent:.0f}% — можно продолжить")
        elif lib is not None and lib.played:
            lines.append("👀 Уже смотрели")
        elif lib is None and jellyfin:
            lines.append("🔄 Jellyfin ещё не добавил файл в медиатеку — обычно это минута")
        elif not jellyfin:
            lines.append("ℹ️ Подключите Jellyfin (JELLYFIN_API_KEY), чтобы запускать фильм на ТВ из бота")
    elif not film.in_radarr:
        lines.append("➕ Нажмите «Скачать» — Radarr найдёт раздачу и сам положит фильм в медиатеку.")
    return "\n".join(lines)


def render_movie_ready(film: Film) -> str:
    meta = _meta(film)
    return f"🍿 <b>{esc(film.title)}</b> скачан и уже в медиатеке" + (f"\n{meta}" if meta else "")


def render_movie_not_found(film: Film) -> str:
    return f"😕 Для <b>{esc(film.label)}</b> пока нет подходящих раздач.\nRadarr продолжит искать сам и скачает, когда появится."


def render_show(show: Show, download: ShowDownload | None, lib: LibraryMovie | None, *, jellyfin: bool) -> str:
    lines = [f"📺 <b>{esc(show.title)}</b>"]
    meta = _meta(show)
    if meta:
        lines.append(meta)
    details = [genres_label(show.genres), show.network, f"серия ~{runtime_label(show.runtime)}" if show.runtime else ""]
    details = [d for d in details if d]
    if details:
        lines.append("🎭 " + esc(" · ".join(details)))
    lines.append(DIV)
    if show.overview:
        lines.append(esc(truncate(show.overview, 650)))
        lines.append("")
    status = show_status(show, download)
    if status:
        lines.append(status)
    if show.has_file:
        if lib is None and jellyfin:
            lines.append("🔄 Jellyfin ещё не добавил серии в медиатеку — обычно это минута")
        elif not jellyfin:
            lines.append("ℹ️ Подключите Jellyfin (JELLYFIN_API_KEY), чтобы запускать серии на ТВ из бота")
    elif not show.in_sonarr:
        lines.append("➕ Скачать все сезоны или только последний — Sonarr найдёт раздачи и разложит по сезонам.")
    return "\n".join(lines)


def render_show_new_episodes(show: Show, added: int) -> str:
    total = f" (всего {show.episodes_have} из {show.episodes_total})" if show.episodes_total else ""
    return f"🍿 <b>{esc(show.title)}</b>: новых серий — {added}{total}. Уже в медиатеке"


def render_show_not_found(show: Show) -> str:
    return f"😕 Для <b>{esc(show.label)}</b> пока нет подходящих раздач.\nSonarr продолжит искать сам и скачает, когда появятся."
