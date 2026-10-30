from homebot.utils.formatting import bar, fmt_bytes, fmt_duration, split_message, truncate


def test_fmt_bytes():
    assert fmt_bytes(0) == "0 Б"
    assert fmt_bytes(1023) == "1023 Б"
    assert fmt_bytes(1536) == "1.5 КБ"
    assert fmt_bytes(5 * 1024**3) == "5.0 ГБ"
    assert fmt_bytes(1024**5) == "1.0 ПБ"
    assert fmt_bytes(1024**6) == "1024.0 ПБ"  # дальше самой крупной единицы не растём, но и не падаем


def test_fmt_duration():
    assert fmt_duration(0) == "0м"
    assert fmt_duration(59) == "0м"
    assert fmt_duration(3 * 3600 + 5 * 60) == "3ч 5м"
    assert fmt_duration(93_784) == "1д 2ч 3м"
    assert fmt_duration(-5) == "0м"  # отрицательные значения не ломают вывод


def test_bar_bounds():
    assert bar(0) == "▱" * 10
    assert bar(100) == "▰" * 10
    assert bar(40) == "▰" * 4 + "▱" * 6
    assert bar(-20) == bar(0)
    assert bar(250) == bar(100)
    assert len(bar(55, width=20)) == 20


def test_truncate():
    assert truncate("  много   пробелов  ", 50) == "много пробелов"
    assert truncate("abcdefghij", 5) == "abcd…"
    assert len(truncate("x" * 100, 10)) == 10


def test_split_message_short_text_is_untouched():
    assert split_message("привет") == ["привет"]
    assert split_message("   ") == []


def test_split_message_respects_limit_and_keeps_all_text():
    text = "\n".join(f"строка {i} " + "а" * 60 for i in range(200))
    chunks = split_message(text, limit=500)
    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)
    # ничего не потеряно (пробельные символы на границах нормализуются)
    assert "".join(c.replace("\n", "").replace(" ", "") for c in chunks) == text.replace("\n", "").replace(" ", "")


def test_split_message_without_spaces_cuts_hard():
    chunks = split_message("x" * 1000, limit=300)
    assert [len(c) for c in chunks] == [300, 300, 300, 100]
