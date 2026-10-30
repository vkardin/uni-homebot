from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from homebot.utils.timing import seconds_until

KYIV = ZoneInfo("Europe/Kyiv")


def test_later_today():
    now = datetime(2026, 10, 13, 6, 0, tzinfo=KYIV)
    assert seconds_until(time(8, 0), KYIV, now=now) == 2 * 3600


def test_already_passed_goes_to_tomorrow():
    now = datetime(2026, 10, 13, 9, 0, tzinfo=KYIV)
    assert seconds_until(time(8, 0), KYIV, now=now) == 23 * 3600


def test_exact_moment_is_not_now():
    # ровно 08:00 -> следующий запуск завтра, а не «через 0 секунд» (иначе дайджест уйдёт дважды)
    now = datetime(2026, 10, 13, 8, 0, tzinfo=KYIV)
    assert seconds_until(time(8, 0), KYIV, now=now) == 24 * 3600


def test_naive_utc_now_is_converted_to_target_zone():
    # 04:00 UTC = 07:00 по Киеву (октябрь, UTC+3) -> до 08:00 остаётся час
    now = datetime(2026, 10, 13, 4, 0, tzinfo=UTC)
    assert seconds_until(time(8, 0), KYIV, now=now) == 3600


def test_dst_change_day_is_handled_in_real_elapsed_time():
    # В Киеве 25.10.2026 часы переводят назад (04:00 -> 03:00): сутки длятся 25 часов.
    now = datetime(2026, 10, 24, 9, 0, tzinfo=KYIV)
    assert seconds_until(time(8, 0), KYIV, now=now) == 24 * 3600
    after_midnight = datetime(2026, 10, 25, 1, 0, tzinfo=KYIV)
    assert seconds_until(time(8, 0), KYIV, now=after_midnight) == 8 * 3600  # 7 «часов на стене» + лишний час
