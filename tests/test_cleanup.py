import asyncio
import time

from homebot.services.apps_http import AppError
from homebot.services.cleanup import (
    KIND_MOVIE,
    KIND_SHOW,
    Watched,
    delete_watched,
    find_watched,
    forecast_full,
    pick_watched,
    signature,
)
from homebot.services.jellyfin import LibraryMovie, parse_library
from homebot.services.radarr import Film, parse_film
from homebot.services.sonarr import Show, parse_show
from homebot.ui.extra_texts import render_cleanup, render_cleanup_all_confirm, render_cleanup_confirm, render_forecast

NOW = int(time.time())
DAY = 86400


def jelly(kind="Movie", *, tmdb=0, tvdb=0, played=True, ago_days=10, title="X"):
    return LibraryMovie(
        id="i", title=title, year=2020, tmdb_id=tmdb, played=played, resume_ticks=0, runtime_ticks=0,
        tvdb_id=tvdb, kind=kind, last_played=NOW - ago_days * DAY if ago_days is not None else 0,
    )  # fmt: skip


def film(tmdb, rid, size, title="Film"):
    return Film(tmdb_id=tmdb, title=title, year=2020, radarr_id=rid, has_file=size > 0, size=size)


def show(tvdb, sid, size, title="Show"):
    return Show(tvdb_id=tvdb, title=title, year=2019, sonarr_id=sid, episodes_have=3, episodes_total=3, size=size)


def test_pick_watched_joins_filters_and_sorts_by_size():
    items = pick_watched(
        [
            jelly(tmdb=1, title="small"),
            jelly(tmdb=2, title="big"),
            jelly(tmdb=3, played=False),  # не смотрели
            jelly(tmdb=4, ago_days=1),  # досмотрели вчера — рано
            jelly(tmdb=5),  # в Radarr нет
            jelly("Series", tvdb=77, title="series"),
            jelly(tmdb=6, ago_days=None),  # дата неизвестна — предлагаем
        ],
        [film(1, 11, 2_000), film(2, 12, 9_000), film(3, 13, 5), film(4, 14, 5), film(6, 16, 100), film(8, 18, 0)],
        [show(77, 31, 5_000)],
        min_days=3,
        now=NOW,
    )
    assert [(i.kind, i.ref, i.size) for i in items] == [
        (KIND_MOVIE, 12, 9_000),
        (KIND_SHOW, 31, 5_000),
        (KIND_MOVIE, 11, 2_000),
        (KIND_MOVIE, 16, 100),
    ]
    assert items[0].watched_days == 10 and items[-1].watched_days is None


def test_pick_watched_ignores_files_already_gone_and_unmatched_ids():
    assert pick_watched([jelly(tmdb=1)], [film(1, 11, 0)], [], now=NOW) == []  # размер 0 — удалять нечего
    assert pick_watched([jelly(tmdb=0)], [film(1, 11, 100)], [], now=NOW) == []  # у Jellyfin нет tmdb-id
    assert pick_watched([jelly("Series", tvdb=0)], [], [show(1, 2, 100)], now=NOW) == []


def test_signature_depends_on_content_and_order():
    a = Watched(KIND_MOVIE, 1, "A", 2020, 10, 5)
    b = Watched(KIND_SHOW, 2, "B", 2020, 20, 5)
    assert signature([a, b]) == signature([a, b])
    assert signature([a, b]) != signature([b, a])
    assert signature([a]) != signature([a, b])
    assert len(signature([a])) == 8


class FakeJelly:
    def __init__(self, movies, series, fail=False):
        self._m, self._s, self.fail = movies, series, fail

    async def movies(self, kind="Movie"):
        if self.fail:
            raise AppError("Jellyfin лежит")
        return self._m if kind == "Movie" else self._s


class FakeArr:
    def __init__(self, library, fail_on=()):
        self._library, self.fail_on, self.deleted = library, set(fail_on), []

    async def library(self):
        return self._library

    async def delete(self, ref):
        if ref in self.fail_on:
            raise AppError("HTTP 500")
        self.deleted.append(ref)


def test_find_watched_survives_one_service_down_but_not_all():
    async def scenario():
        j = FakeJelly([jelly(tmdb=1)], [jelly("Series", tvdb=9)])
        radarr = FakeArr([film(1, 11, 100)])

        class BrokenSonarr:
            async def library(self):
                raise AppError("Sonarr недоступен")

        found = await find_watched(j, radarr, BrokenSonarr(), min_days=0)  # type: ignore[arg-type]
        assert [i.ref for i in found] == [11]

        try:
            await find_watched(FakeJelly([], [], fail=True), radarr, None, min_days=0)  # type: ignore[arg-type]
        except AppError as exc:
            assert "Jellyfin" in str(exc)
        else:
            raise AssertionError("ошибка должна быть показана")

    asyncio.run(scenario())


def test_delete_watched_counts_and_collects_errors():
    async def scenario():
        radarr, sonarr = FakeArr([], fail_on={12}), FakeArr([])
        items = [
            Watched(KIND_MOVIE, 11, "A", 2020, 100, 5),
            Watched(KIND_MOVIE, 12, "B", 2020, 500, 5),
            Watched(KIND_SHOW, 31, "C", None, 300, 5),
        ]
        removed, freed, errors = await delete_watched(radarr, sonarr, items)  # type: ignore[arg-type]
        assert (removed, freed) == (2, 400)
        assert radarr.deleted == [11] and sonarr.deleted == [31]
        assert len(errors) == 1 and "B (2020)" in errors[0]

        removed, freed, errors = await delete_watched(None, None, items[:1])
        assert (removed, freed) == (0, 0) and "не подключён" in errors[0]

    asyncio.run(scenario())


def test_forecast_full_linear_growth_and_edge_cases():
    day = 86400
    growing = [(i * 3600, 50 + i * 3600 / day * 1.0) for i in range(24 * 5)]  # +1 %/сутки
    result = forecast_full(growing)
    assert result is not None
    assert abs(result.per_day - 1.0) < 0.01
    assert 40 < result.days_left < 50  # около 45 суток до 100 %

    flat = [(i * 3600, 60.0) for i in range(24 * 5)]
    assert forecast_full(flat) is None
    shrinking = [(i * 3600, 80 - i * 0.01) for i in range(24 * 5)]
    assert forecast_full(shrinking) is None
    assert forecast_full(growing[:5]) is None  # мало точек
    short = [(i * 60, 50 + i * 0.01) for i in range(60)]
    assert forecast_full(short) is None  # меньше полутора суток истории
    full = forecast_full([(i * 3600, 99.9 + i * 0.001) for i in range(24 * 5)] + [(10**6, 100.0)])
    assert full is None or full.days_left >= 0


def test_forecast_ignores_out_of_range_points():
    pts = [(i * 3600, 50 + i / 24) for i in range(24 * 5)] + [(10**7, 150.0)]
    assert forecast_full(pts) is not None


def test_parse_sizes_and_last_played():
    assert parse_film({"tmdbId": 5, "title": "F", "sizeOnDisk": 123, "id": 2, "hasFile": True}).size == 123
    parsed = parse_show({"tvdbId": 7, "title": "S", "statistics": {"sizeOnDisk": 456, "seasonCount": 1}, "id": 3})
    assert parsed is not None and parsed.size == 456
    items = parse_library(
        {"Items": [{"Id": "a", "Name": "M", "UserData": {"Played": True, "LastPlayedDate": "2026-10-01T18:22:11.1234567Z"},
                    "ProviderIds": {"Tmdb": "5"}}, {"Id": "b", "Name": "N", "UserData": {"LastPlayedDate": "мусор"}}]}
    )  # fmt: skip
    assert items[0].last_played > 1_700_000_000 and items[1].last_played == 0


def test_render_cleanup_texts_escape_and_summarize():
    items = [
        Watched(KIND_MOVIE, 1, "Tom & <Jerry>", 2001, 12 * 1024**3, 40),
        Watched(KIND_SHOW, 2, "Show", None, 3 * 1024**3, None),
    ]
    text = render_cleanup(items, disk_percent=81.0, disk_free=200 * 1024**3, forecast=forecast_full(
        [(i * 3600, 50 + i / 24) for i in range(24 * 5)]
    ), min_days=3)  # fmt: skip
    assert "Tom &amp; &lt;Jerry&gt;" in text and "12.0 ГБ" in text and "40 дн. назад" in text
    assert "Можно освободить: <b>15.0 ГБ</b>" in text and "место кончится" in text and "81 %" in text
    assert "удалять нечего" in render_cleanup([], min_days=3)
    assert "Show" in render_cleanup_all_confirm(items) and "15.0 ГБ" in render_cleanup_all_confirm(items)
    assert "сериал" in render_cleanup_confirm(items[1]) and "фильм" in render_cleanup_confirm(items[0])
    assert render_forecast(None) == ""
