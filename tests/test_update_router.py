"""Кнопка «Установить» в чате должна запускать установку, а не только бэкап (ошибка с 6.5.0)."""

from __future__ import annotations

from homebot.handlers import update


def _callbacks() -> list[str]:
    return [h.callback.__name__ for h in update.router.callback_query.handlers]


def test_install_button_runs_cb_go():
    names = _callbacks()
    assert "cb_go" in names
    assert "_backup_before_update" not in names
