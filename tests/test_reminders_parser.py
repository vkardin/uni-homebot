from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homebot.services.reminders import parse_reminder

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 10, 13, 10, 30, 15, tzinfo=KYIV)  # вторник


def parse(text):
    return parse_reminder(text, NOW)


def test_relative_minutes_hours_days():
    assert parse("через 15 минут проверить бэкап").due == NOW + timedelta(minutes=15)
    assert parse("через 2 часа выключить чайник").due == NOW + timedelta(hours=2)
    assert parse("через час позвонить").due == NOW + timedelta(hours=1)
    assert parse("через полчаса тест").due == NOW + timedelta(minutes=30)
    assert parse("через 3 дня оплатить").due == NOW + timedelta(days=3)
    assert parse("через неделю отчёт").due == NOW + timedelta(weeks=1)
    assert parse("через 10 хв перевірити").due == NOW + timedelta(minutes=10)
    reminder = parse("через 5 мин   снять   пиццу")
    assert reminder.text == "снять   пиццу"


def test_day_words():
    r = parse("завтра 9:00 оплатить интернет")
    assert r.due == datetime(2026, 10, 14, 9, 0, tzinfo=KYIV) and r.text == "оплатить интернет"
    assert parse("завтра в 18.45 кино").due == datetime(2026, 10, 14, 18, 45, tzinfo=KYIV)
    assert parse("завтра купить молоко").due == datetime(2026, 10, 14, 9, 0, tzinfo=KYIV)  # время по умолчанию
    assert parse("послезавтра 7:15 поезд").due == datetime(2026, 10, 15, 7, 15, tzinfo=KYIV)
    assert parse("сегодня 23:00 спать").due == datetime(2026, 10, 13, 23, 0, tzinfo=KYIV)


def test_word_v_is_not_eaten_when_there_is_no_time():
    assert parse("завтра в магазин").text == "в магазин"


def test_at_time_rolls_to_tomorrow_if_passed():
    assert parse("в 18:30 позвонить маме").due == datetime(2026, 10, 13, 18, 30, tzinfo=KYIV)
    assert parse("в 9:00 зарядка").due == datetime(2026, 10, 14, 9, 0, tzinfo=KYIV)


def test_explicit_date():
    assert parse("15.10 10:00 продлить домен").due == datetime(2026, 10, 15, 10, 0, tzinfo=KYIV)
    assert parse("1.01 новый год").due == datetime(2027, 1, 1, 9, 0, tzinfo=KYIV)  # прошла — следующий год
    assert parse("20.12.2026 в 12:00 подарки").due == datetime(2026, 12, 20, 12, 0, tzinfo=KYIV)


def test_weekdays():
    assert parse("в пятницу 19:00 кино").due == datetime(2026, 10, 16, 19, 0, tzinfo=KYIV)
    assert parse("понедельник созвон").due == datetime(2026, 10, 19, 9, 0, tzinfo=KYIV)
    assert parse("во вторник 8:00 рано").due == datetime(2026, 10, 20, 8, 0, tzinfo=KYIV)  # сегодня уже прошло
    assert parse("вторник 20:00 вечер").due == datetime(2026, 10, 13, 20, 0, tzinfo=KYIV)


def test_rejections():
    for bad in (
        "",
        "просто текст без времени",
        "через 5 минут",  # нет текста
        "сегодня 8:00 уже было",  # прошлое
        "сегодня позвонить",  # «сегодня» без времени — непонятно когда
        "завтра 25:00 странно",
        "в 12:75 странно",
        "31.02 нет такой даты",
        "01.01.2020 давно",
        "через 5 лет что-то",
        "через 0 минут сейчас",
    ):
        assert parse(bad) is None, bad


def test_case_insensitive_and_text_trimmed():
    r = parse("ЗАВТРА 10:00 Важная встреча  ")
    assert r.due.day == 14 and r.text == "Важная встреча"
