"""Кино: Radarr, библиотека и пульт Jellyfin, подбор фильмов моделью."""

import asyncio
import json

import pytest

from homebot.services.apps_http import AppError
from homebot.services.jellyfin import (
    JellyfinService,
    LibraryMovie,
    Screen,
    find_by_tmdb,
    parse_library,
    parse_screens,
    pick_screen,
)
from homebot.services.movie_picks import best_match, parse_picks, recommend
from homebot.services.radarr import Film, RadarrService, _pick_profile, human_timeleft, parse_film, parse_films, parse_queue
from tests.fakes import FakeResponse, FakeSession

LOOKUP_ITEM = {
    "title": "Dune: Part Two",
    "originalTitle": "Dune: Part Two",
    "year": 2024,
    "overview": "Paul Atreides unites with Chani…",
    "runtime": 167,
    "genres": ["Science Fiction", "Adventure", "Drama", "Action"],
    "ratings": {"imdb": {"value": 8.5}, "tmdb": {"value": 8.2}},
    "images": [{"coverType": "fanart", "remoteUrl": "https://img/f.jpg"}, {"coverType": "poster", "remoteUrl": "https://img/p.jpg"}],
    "tmdbId": 693134,
    "status": "released",
}


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ Radarr: разбор
def test_parse_film_from_lookup():
    film = parse_film(LOOKUP_ITEM)
    assert film is not None
    assert film.label == "Dune: Part Two (2024)"
    assert film.rating == 8.5 and film.poster == "https://img/p.jpg"
    assert film.genres == ("Science Fiction", "Adventure", "Drama")
    assert not film.in_radarr and not film.has_file


def test_parse_film_from_library_and_garbage():
    film = parse_film({**LOOKUP_ITEM, "id": 7, "hasFile": True, "ratings": {}, "images": [], "remotePoster": "https://img/r.jpg"})
    assert film is not None and film.in_radarr and film.has_file and film.rating is None and film.poster == "https://img/r.jpg"
    assert parse_film({"title": "без tmdb"}) is None
    assert parse_film("x") is None
    with pytest.raises(AppError):
        parse_films({"oops": 1})


def test_parse_films_dedupes():
    assert len(parse_films([LOOKUP_ITEM, LOOKUP_ITEM, {"tmdbId": 1, "title": "B"}])) == 2


def test_human_timeleft():
    cases = [
        ("00:12:30", "12 мин"),
        ("01:05:00", "1 ч 5 мин"),
        ("1.02:00:00", "1 д 2 ч"),
        ("00:00:10", "1 мин"),
        (None, ""),
        ("bad", ""),
    ]
    for raw, expected in cases:
        assert human_timeleft(raw) == expected, raw


def test_parse_queue_progress_and_problem():
    data = {
        "records": [
            {
                "movieId": 1,
                "title": "Tenet.mkv",
                "size": 1000,
                "sizeleft": 250,
                "timeleft": "00:10:00",
                "trackedDownloadState": "downloading",
            },
            {
                "movieId": 2,
                "title": "Spider.mkv",
                "size": 500,
                "sizeleft": 0,
                "trackedDownloadState": "importBlocked",
                "statusMessages": [{"title": "x", "messages": ["Root folder missing."]}],
            },
            {"movieId": 1, "title": "dup", "size": 1000, "sizeleft": 900},  # хуже первой — игнор
            {"title": "без фильма"},
        ]
    }
    queue = parse_queue(data)
    assert set(queue) == {1, 2}
    assert queue[1].progress == 75 and queue[1].timeleft == "10 мин" and queue[1].title == "Tenet.mkv"
    assert queue[2].progress == 100 and queue[2].problem == "Root folder missing."


def test_pick_profile_prefers_named_then_1080():
    profiles = [{"id": 1, "name": "Any"}, {"id": 4, "name": "HD-1080p"}, {"id": 5, "name": "Ultra-HD"}]
    assert _pick_profile(profiles, "ultra-hd") == 5
    assert _pick_profile(profiles, "") == 4
    assert _pick_profile([{"id": 9, "name": "Any"}], "") == 9
    with pytest.raises(AppError):
        _pick_profile([], "")


def _radarr_session(posted: list):
    def handler(method, url, kwargs):
        params = kwargs.get("params") or {}
        if url.endswith("/api/v3/movie") and method == "GET":
            return FakeResponse(body=[])  # в библиотеке ещё нет
        if url.endswith("/movie/lookup/tmdb"):
            assert params["tmdbId"] == "693134"
            return FakeResponse(body=LOOKUP_ITEM)
        if url.endswith("/qualityprofile"):
            return FakeResponse(body=[{"id": 1, "name": "Any"}, {"id": 4, "name": "HD-1080p"}])
        if url.endswith("/rootfolder"):
            return FakeResponse(body=[{"path": "/data/media/movies", "accessible": True}])
        if url.endswith("/api/v3/movie") and method == "POST":
            posted.append(kwargs["json"])
            return FakeResponse(body={**LOOKUP_ITEM, "id": 42})
        raise AssertionError(url)

    return FakeSession(handler)


def test_radarr_add_sends_profile_root_and_search():
    posted: list = []
    session = _radarr_session(posted)
    film = run(RadarrService(session, "http://r:7878/", "KEY").add(693134))
    assert film.radarr_id == 42
    body = posted[0]
    assert body["qualityProfileId"] == 4 and body["rootFolderPath"] == "/data/media/movies"
    assert body["addOptions"] == {"searchForMovie": True, "monitor": "movieOnly"} and body["monitored"] is True
    assert all(call[2]["headers"]["X-Api-Key"] == "KEY" for call in session.calls)


def test_radarr_add_existing_is_noop():
    def handler(method, url, kwargs):
        assert method == "GET"
        return FakeResponse(body=[{**LOOKUP_ITEM, "id": 3}])

    film = run(RadarrService(FakeSession(handler), "http://r", "K").add(693134))
    assert film.radarr_id == 3


def test_radarr_discover_skips_existing():
    def handler(method, url, kwargs):
        assert url.endswith("/importlist/movie") and kwargs["params"]["includeTrending"] == "true"
        return FakeResponse(body=[{**LOOKUP_ITEM, "isExisting": True}, {"tmdbId": 5, "title": "New", "year": 2026}])

    films = run(RadarrService(FakeSession(handler), "http://r", "K").discover())
    assert [f.tmdb_id for f in films] == [5]


# ------------------------------------------------------------------ Jellyfin
def test_parse_library_and_find():
    data = {
        "Items": [
            {
                "Id": "abc",
                "Name": "Довод",
                "ProductionYear": 2020,
                "ProviderIds": {"Tmdb": "577922"},
                "RunTimeTicks": 9_000_000_000,
                "UserData": {"PlaybackPositionTicks": 3_000_000_000, "Played": False},
            },
            {"Id": "def", "Name": "Без метаданных", "ProviderIds": {}, "UserData": {"Played": True}},
            {"Name": "без Id"},
        ]
    }
    movies = parse_library(data)
    assert len(movies) == 2
    assert movies[0].tmdb_id == 577922 and movies[0].resume_ticks == 3_000_000_000
    assert round(movies[0].resume_percent) == 33
    assert movies[1].played and movies[1].tmdb_id == 0
    assert find_by_tmdb(movies, 577922).id == "abc"
    assert find_by_tmdb(movies, 1, "без метаданных").id == "def"
    assert find_by_tmdb(movies, 1, "Другое") is None


def test_parse_screens_and_pick():
    data = [
        {"Id": "web", "DeviceName": "Chrome", "Client": "Jellyfin Web", "SupportsRemoteControl": True},
        {"Id": "tv", "DeviceName": "Mi TV Stick", "Client": "Android TV", "SupportsRemoteControl": True},
        {"Id": "api", "DeviceName": "homebot", "SupportsRemoteControl": False},
    ]
    screens = parse_screens(data)
    assert [s.id for s in screens] == ["web", "tv"]
    assert pick_screen(screens).id == "tv"
    assert pick_screen(screens, prefer="chrome").id == "web"
    assert pick_screen(screens, prefer="нет такого").id == "tv"
    assert pick_screen([]) is None


def test_pick_screen_prefers_what_is_playing():
    playing = Screen("web", "Chrome", "Jellyfin Web", "Tenet", 10, 100, False, None)
    idle_tv = Screen("tv", "Mi TV Stick", "Android TV", "", 0, 0, False, None)
    assert pick_screen([idle_tv, playing]).id == "web"


def _jellyfin(handler):
    return JellyfinService(FakeSession(handler), "http://j:8096", "KEY")


def test_jellyfin_play():
    calls = []

    def handler(method, url, kwargs):
        calls.append((method, url, kwargs.get("params")))
        return FakeResponse(raw=b"")

    jf = _jellyfin(handler)
    run(jf.play("tv", "abc", start_ticks=50))
    assert calls[0] == (
        "POST",
        "http://j:8096/Sessions/tv/Playing",
        {"playCommand": "PlayNow", "itemIds": "abc", "startPositionTicks": "50"},
    )


def test_jellyfin_movies_uses_admin_user():
    def handler(method, url, kwargs):
        if url.endswith("/Users"):
            return FakeResponse(body=[{"Id": "u1", "Policy": {}}, {"Id": "admin", "Policy": {"IsAdministrator": True}}])
        assert kwargs["params"]["userId"] == "admin"
        return FakeResponse(body={"Items": [{"Id": "x", "Name": "Film", "ProviderIds": {"Tmdb": "5"}}]})

    jf = _jellyfin(handler)
    assert run(jf.movies())[0].tmdb_id == 5
    assert run(jf.movies())[0].id == "x"  # id пользователя закэширован


# ------------------------------------------------------------------ подбор
class FakeAsker:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def ask(self, system, text, *, model=None, schema=None):
        self.prompts.append(text)
        return self.answers.pop(0)


class FakeLookup:
    def __init__(self, table: dict[str, list[Film]]) -> None:
        self.table = table
        self.terms: list[str] = []

    async def lookup(self, term: str) -> list[Film]:
        self.terms.append(term)
        if term == "boom":
            raise AppError("down")
        return self.table.get(term, [])


def test_parse_picks_tolerates_noise():
    text = (
        'Вот: ```json\n{"movies": [{"title": "Heat", "year": 1995, "why": "классика"}, {"title": ""}, {"title": "X", "year": 3}]}\n```'
    )
    picks = parse_picks(text)
    assert [(p.title, p.year) for p in picks] == [("Heat", 1995), ("X", None)]
    assert parse_picks("не json") == []


def test_best_match_year_tolerance():
    a, b = Film(1, "A", year=1999), Film(2, "B", year=2010)
    assert best_match([a, b], 2011) is b
    assert best_match([a, b], 2005) is None
    assert best_match([a, b], None) is a
    assert best_match([], 2000) is None


def test_recommend_keeps_only_real_films():
    heat, alien = Film(10, "Heat", year=1995), Film(20, "Alien", year=1979)
    lookup = FakeLookup({"Heat 1995": [heat], "Alien 1979": [alien], "Fake 2020": [Film(30, "Other", year=1960)]})
    answer = json.dumps(
        {
            "movies": [
                {"title": "Heat", "year": 1995, "why": "перестрелки"},
                {"title": "Fake", "year": 2020, "why": "выдумка"},
                {"title": "boom", "year": 0, "why": "ошибка поиска"},
                {"title": "Heat", "year": 1995, "why": "дубль"},
                {"title": "Alien", "year": 1979, "why": "страшно"},
            ]
        }
    )
    asker = FakeAsker(answer)
    result = run(recommend(lookup, asker, "напряжённое", count=5, exclude=("Tenet",)))
    assert [(f.tmdb_id, why) for f, why in result] == [(10, "перестрелки"), (20, "страшно")]
    assert "Tenet" in asker.prompts[0]


def test_recommend_empty_answer_is_error():
    with pytest.raises(AppError):
        run(recommend(FakeLookup({}), FakeAsker('{"movies": []}'), "x"))


# ------------------------------------------------------------------ уведомления
class JobBot:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []
        self.fail = False

    async def send_message(self, chat_id, text, reply_markup=None):
        if self.fail:
            from aiogram.exceptions import TelegramAPIError

            raise TelegramAPIError(method=None, message="down")  # type: ignore[arg-type]
        self.sent.append((text, reply_markup))


class FakeArr:
    def __init__(self, library, queue=None):
        self.lib = library
        self.q = queue or {}

    async def library(self):
        return self.lib

    async def queue(self):
        return self.q


def _store(tmp: str):
    from pathlib import Path

    from homebot.services.state import StateStore

    store = StateStore(Path(tmp) / "state.json")
    store.load()
    return store


def _job(bot, store, radarr=None, sonarr=None):
    from homebot.jobs.movies_watch import MoviesWatchJob

    return MoviesWatchJob(
        bot=bot,  # type: ignore[arg-type]
        admin_id=1,
        store=store,
        radarr=radarr,  # type: ignore[arg-type]
        sonarr=sonarr,  # type: ignore[arg-type]
        ready_markup=lambda ref, series=False: ("ready", ref, series),
        retry_markup=lambda ref, series=False: ("retry", ref, series),
    )


def test_movies_job_film_ready_once_and_quiet_start():
    import tempfile

    from homebot.services.radarr import Film

    with tempfile.TemporaryDirectory() as tmp:
        store, bot = _store(tmp), JobBot()
        old = Film(1, "Tenet", year=2020, radarr_id=1, has_file=True)
        new = Film(2, "Dune", year=2021, radarr_id=2, has_file=False)
        radarr = FakeArr([old, new])
        job = _job(bot, store, radarr=radarr)
        run(job.tick(now=0))
        assert bot.sent == []  # первый запуск — молча
        radarr.lib = [old, Film(2, "Dune", year=2021, radarr_id=2, has_file=True)]
        bot.fail = True
        run(job.tick(now=10))
        assert bot.sent == []  # не доставили — попробуем ещё
        bot.fail = False
        run(job.tick(now=20))
        run(job.tick(now=30))
        assert len(bot.sent) == 1
        text, markup = bot.sent[0]
        assert "Dune" in text and "скачан" in text and markup == ("ready", 2, False)


def test_movies_job_series_counts_new_episodes():
    import tempfile

    from homebot.services.sonarr import Show

    with tempfile.TemporaryDirectory() as tmp:
        store, bot = _store(tmp), JobBot()
        sonarr = FakeArr([Show(100, "The Boys", sonarr_id=5, episodes_have=2, episodes_total=8)])
        job = _job(bot, store, sonarr=sonarr)
        run(job.tick(now=0))
        sonarr.lib = [
            Show(100, "The Boys", sonarr_id=5, episodes_have=5, episodes_total=8),
            Show(200, "Fresh", sonarr_id=6, episodes_have=1),
        ]
        run(job.tick(now=10))
        run(job.tick(now=20))
        texts = [t for t, _ in bot.sent]
        assert len(texts) == 2
        assert "The Boys" in texts[0] and "3" in texts[0] and "5 из 8" in texts[0]
        assert "Fresh" in texts[1] and bot.sent[1][1] == ("ready", 200, True)


def test_movies_job_not_found_after_30_min():
    import tempfile

    from homebot.jobs.movies_watch import KEY_REQUESTED
    from homebot.services.radarr import Download, Film

    with tempfile.TemporaryDirectory() as tmp:
        store, bot = _store(tmp), JobBot()
        lost = Film(7, "Lost Film", year=1999, radarr_id=7)
        found = Film(8, "Found", year=2000, radarr_id=8)
        radarr = FakeArr([lost, found], {8: Download(8, "x", 10, 50.0, "", "downloading", "")})
        job = _job(bot, store, radarr=radarr)
        run(store.set(KEY_REQUESTED, {"m:7": 0, "m:8": 0, "m:99": 0, "junk": 0}))
        run(job.tick(now=60))
        assert bot.sent == [] and set(store.get(KEY_REQUESTED)) == {"m:7", "m:99", "junk"}
        run(job.tick(now=31 * 60))
        assert len(bot.sent) == 1 and "Lost Film" in bot.sent[0][0] and bot.sent[0][1] == ("retry", 7, False)
        assert store.get(KEY_REQUESTED) == {}


def test_torrent_watch_is_quiet_for_arr_categories():
    import tempfile

    from homebot.jobs.apps_watch import TorrentWatchJob
    from homebot.services.qbittorrent import parse_torrents

    class Qbit:
        def __init__(self) -> None:
            self.data: list = []

        async def torrents(self):
            return parse_torrents(self.data)

    class Bot:
        def __init__(self) -> None:
            self.sent: list = []

        async def send_message(self, chat_id, text):
            self.sent.append(text)

    with tempfile.TemporaryDirectory() as tmp:
        store, qbit, bot = _store(tmp), Qbit(), Bot()
        job = TorrentWatchJob(bot=bot, admin_id=1, qbit=qbit, store=store, quiet_categories=frozenset({"movies"}))  # type: ignore[arg-type]
        run(job.tick())
        qbit.data = [
            {"hash": "a", "name": "Film.mkv", "progress": 1, "category": "Movies"},
            {"hash": "b", "name": "Game.iso", "progress": 1, "category": "games"},
        ]
        run(job.tick())
        run(job.tick())
        assert len(bot.sent) == 1 and "Game.iso" in bot.sent[0] and "Film" not in bot.sent[0]
        assert set(store.get(TorrentWatchJob.KEY)) == {"a", "b"}


# ------------------------------------------------------------------ Sonarr
SERIES_ITEM = {
    "title": "The Boys",
    "year": 2019,
    "tvdbId": 355567,
    "overview": "Superheroes…",
    "runtime": 60,
    "genres": ["Action", "Comedy"],
    "ratings": {"votes": 100, "value": 8.7},
    "images": [{"coverType": "poster", "remoteUrl": "https://img/boys.jpg"}],
    "status": "continuing",
    "network": "Prime Video",
    "seasons": [{"seasonNumber": 0}, {"seasonNumber": 1}, {"seasonNumber": 2}, {"seasonNumber": 3}],
}


def test_parse_show_and_status():
    from homebot.services.sonarr import parse_show

    show = parse_show({**SERIES_ITEM, "id": 3, "statistics": {"episodeFileCount": 8, "episodeCount": 24}})
    assert show is not None and show.seasons == 3 and show.rating == 8.7 and show.poster == "https://img/boys.jpg"
    assert show.in_sonarr and show.has_file and not show.complete
    assert parse_show({"title": "no tvdb"}) is None


def test_parse_show_queue_groups_episodes():
    from homebot.services.sonarr import parse_show_queue

    data = {
        "records": [
            {"seriesId": 3, "size": 100, "sizeleft": 50, "timeleft": "00:20:00"},
            {"seriesId": 3, "size": 100, "sizeleft": 0, "statusMessages": [{"messages": ["No files found"]}]},
            {"seriesId": 4, "size": 0},
            {"movieId": 1},
        ]
    }
    queue = parse_show_queue(data)
    assert (
        queue[3].episodes == 2 and queue[3].progress == 75 and queue[3].timeleft == "20 мин" and queue[3].problem == "No files found"
    )
    assert queue[4].progress == 0


def test_sonarr_add_latest_season_with_language_profile():
    from homebot.services.sonarr import MONITOR_LATEST, SonarrService

    posted = []

    def handler(method, url, kwargs):
        params = kwargs.get("params") or {}
        if url.endswith("/api/v3/series") and method == "GET":
            return FakeResponse(body=[])
        if url.endswith("/series/lookup"):
            assert params["term"] == "tvdb:355567"
            return FakeResponse(body=[SERIES_ITEM])
        if url.endswith("/qualityprofile"):
            return FakeResponse(body=[{"id": 6, "name": "HD-1080p"}])
        if url.endswith("/rootfolder"):
            return FakeResponse(body=[{"path": "/data/media/shows"}])
        if url.endswith("/languageprofile"):
            return FakeResponse(status=404)  # Sonarr v4
        if method == "POST":
            posted.append(kwargs["json"])
            return FakeResponse(body={**SERIES_ITEM, "id": 9})
        raise AssertionError(url)

    show = run(SonarrService(FakeSession(handler), "http://s:8989", "K").add(355567, monitor=MONITOR_LATEST))
    assert show.sonarr_id == 9
    body = posted[0]
    assert body["addOptions"]["monitor"] == "latestSeason" and body["addOptions"]["searchForMissingEpisodes"] is True
    assert body["rootFolderPath"] == "/data/media/shows" and body["qualityProfileId"] == 6 and "languageProfileId" not in body
    with pytest.raises(AppError):
        run(SonarrService(FakeSession(handler), "http://s", "K").add(1, monitor="everything"))


def test_movie_texts_render():
    from homebot.services.radarr import Download, Film
    from homebot.services.sonarr import Show, ShowDownload
    from homebot.ui.movie_texts import render_film, render_film_list, render_movies_home, render_show

    film = Film(1, "Tenet <x>", "Tenet", 2020, "Spy story", 150, ("Action", "Science Fiction"), 7.3, "", 1, True)
    lib = LibraryMovie("j1", "Tenet", 2020, 1, False, 50, 100)
    text = render_film(film, None, lib, jellyfin=True)
    assert "&lt;x&gt;" in text and "2 ч 30 мин" in text and "боевик, фантастика" in text and "Остановились на 50%" in text
    dl = Download(2, "Dune.mkv", 10, 42.0, "12 мин", "downloading", "")
    listing = render_film_list(
        "🔍", [(Film(2, "Dune", year=2021, radarr_id=2), "", dl), (Show(5, "Boys", 2019, seasons=3), "смешно", None)]
    )
    assert "42%" in listing and "осталось 12 мин" in listing and "📺" in listing and "смешно" in listing
    show_text = render_show(
        Show(5, "Boys", 2019, sonarr_id=1, episodes_have=3, episodes_total=10), ShowDownload(1, 2, 50.0, "", ""), None, jellyfin=True
    )
    assert "Качается серий: 2" in show_text
    home = render_movies_home([("🎬 Dune", 42.0, "12 мин", False)], None, "фильмов — 3", ["Sonarr: down"])
    assert "Dune — 42% · 12 мин" in home and "не на связи" in home and "Sonarr: down" in home
