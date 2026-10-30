# Contributing to UNI

UNI is a Telegram bot and Mini App panel for a home server. Every user runs their own copy with their own bot token.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -q && ruff check . && ruff format --check .
```

## Rules

- Keep changes small: one feature or fix per pull request, with a test.
- Never break an existing install. A change to `state.json`, `.env` or the systemd units needs a migration
  and a note in the changelog (`homebot/changelog.py`) and README.
- aiogram filters and middlewares fail silently and can stop the whole bot: test them twice.
- User-facing text is Russian first. Add English and Ukrainian strings to `homebot/i18n.py`
  (bot) and `TR` in `homebot/web/static/app.js` (panel); anything untranslated falls back to Russian.
- Release archives are named `homebot-X.Y.Z.tar.gz`.

## Layout

`homebot/handlers` (chat), `homebot/services` (logic), `homebot/web` (panel API and static files),
`homebot/jobs` (background tasks), `deploy` (install, update, systemd), `tests`.
