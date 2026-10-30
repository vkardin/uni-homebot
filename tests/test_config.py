from pydantic import ValidationError

from homebot.config import Settings, _split_list

BASE = {"bot_token": "123:abc", "admin_id": 42}


def make(**overrides) -> Settings:
    # _env_file=None: тест не должен зависеть от реального .env на машине разработчика
    return Settings(_env_file=None, **{**BASE, **overrides})  # type: ignore[call-arg]


def expect_invalid(**overrides) -> None:
    try:
        make(**overrides)
    except ValidationError:
        return
    raise AssertionError(f"ValidationError expected for {overrides!r}")


def test_defaults_and_derived_values():
    s = make()
    assert s.admin_id == 42 and s.bot_token.get_secret_value() == "123:abc"
    assert "123:abc" not in repr(s)  # токен не утекает в логи/репр
    assert s.services == ["ssh", "tailscaled", "ollama"]
    assert s.feeds == [] and not s.weather_enabled


def test_list_parsing_accepts_commas_spaces_and_newlines():
    assert _split_list("a, b\nc  d,,") == ["a", "b", "c", "d"]
    s = make(monitored_services="ssh, docker\nollama", rss_feeds="https://a.test/rss,\nftp://bad.test, https://b.test/feed")
    assert s.services == ["ssh", "docker", "ollama"]
    assert s.feeds == ["https://a.test/rss", "https://b.test/feed"]  # не-http(s) отбрасываются


def test_weather_needs_both_coordinates():
    assert not make(weather_lat=50.4).weather_enabled
    assert make(weather_lat=50.4, weather_lon=30.5).weather_enabled


def test_required_fields_and_admin_id_validation():
    try:
        Settings(_env_file=None)  # type: ignore[call-arg]
    except ValidationError as exc:
        missing = {e["loc"][0] for e in exc.errors()}
        assert "bot_token" in missing
        assert "admin_id" not in missing  # без ADMIN_ID — захват владельца по коду
    else:
        raise AssertionError("missing required fields must fail")
    expect_invalid(admin_id=0)
    expect_invalid(admin_id=-5)


def test_validators():
    expect_invalid(timezone="Mars/Olympus")
    expect_invalid(ollama_host="localhost:11434")
    expect_invalid(log_level="LOUD")
    expect_invalid(alert_cpu_percent=0)
    expect_invalid(weather_lat=123)
    assert make(log_level="debug").log_level == "DEBUG"
    assert make(ollama_host="http://127.0.0.1:11434/").ollama_host == "http://127.0.0.1:11434"


def test_libretranslate_url_is_normalised_like_other_addresses():
    assert make(libretranslate_url=" http://127.0.0.1:5000/ ").libretranslate_url == "http://127.0.0.1:5000"
    assert make().libretranslate_url == ""
    expect_invalid(libretranslate_url="127.0.0.1:5000")


def test_guests_flag_tolerates_inline_comment_and_example_file_has_none():
    from pathlib import Path

    assert make(guests_enabled="true  # принимать заявки").guests_enabled is True
    assert make(guests_enabled="false # нет").guests_enabled is False
    assert make().guests_enabled is True
    # комментарий в той же строке, что и значение, ломает чтение .env — в примере их быть не должно
    for line in (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            assert " #" not in line, line
