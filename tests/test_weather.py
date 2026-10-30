from homebot.services.weather import build_params, parse_open_meteo

SAMPLE = {
    "current": {
        "temperature_2m": 11.4,
        "apparent_temperature": 9.8,
        "weather_code": 61,
        "wind_speed_10m": 3.2,
    },
    "daily": {
        "temperature_2m_max": [14.0],
        "temperature_2m_min": [6.5],
        "precipitation_probability_max": [70],
    },
}


def test_parse_ok():
    info = parse_open_meteo(SAMPLE)
    assert info.temperature == 11.4
    assert info.feels_like == 9.8
    assert info.precip_prob == 70
    assert info.emoji == "🌧"
    assert info.description == "небольшой дождь"


def test_missing_precip_probability_is_optional():
    data = {**SAMPLE, "daily": {k: v for k, v in SAMPLE["daily"].items() if k != "precipitation_probability_max"}}
    assert parse_open_meteo(data).precip_prob is None


def test_unknown_code_does_not_crash():
    data = {**SAMPLE, "current": {**SAMPLE["current"], "weather_code": 12345}}
    info = parse_open_meteo(data)
    assert "12345" in info.description


def test_malformed_payload_raises_expected_errors():
    for bad in ({}, {"current": {}}, {"current": SAMPLE["current"], "daily": {}}):
        try:
            parse_open_meteo(bad)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        raise AssertionError(f"expected a parse error for {bad!r}")


def test_params_are_strings():
    params = build_params(50.4501, 30.5234)
    assert params["latitude"] == "50.4501"
    assert all(isinstance(v, str) for v in params.values())
