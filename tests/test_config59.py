"""5.9: ошибка в необязательном параметре .env не роняет бота."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from homebot import config


def _env(monkeypatch, **values):
    monkeypatch.chdir(monkeypatch._tmp)  # без чужого .env
    for key, value in values.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch._tmp = tmp_path  # type: ignore[attr-defined]
    for key in ("BOT_TOKEN", "ADMIN_ID", "LOG_LEVEL", "OLLAMA_HOST", "TIMEZONE"):
        monkeypatch.delenv(key, raising=False)


def test_bad_optional_falls_back(monkeypatch):
    _env(monkeypatch, BOT_TOKEN="1:x", ADMIN_ID="42", LOG_LEVEL="ГРОМКО", TIMEZONE="Mars/Base")
    settings = config.load_settings()
    assert settings.admin_id == 42
    assert settings.log_level == "INFO"
    assert config.LOAD_PROBLEMS == ["LOG_LEVEL", "TIMEZONE"]
    assert "log_level" not in settings.model_fields_set


def test_good_config_has_no_problems(monkeypatch):
    _env(monkeypatch, BOT_TOKEN="1:x")
    config.load_settings()
    assert config.LOAD_PROBLEMS == []


def test_critical_errors_still_fail(monkeypatch):
    _env(monkeypatch, ADMIN_ID="-1", LOG_LEVEL="ГРОМКО")
    with pytest.raises(ValidationError):
        config.load_settings()
