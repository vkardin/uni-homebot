"""5.6: владелец без ADMIN_ID — код из журнала, owner.json."""

from __future__ import annotations

import stat

from homebot.config import Settings
from homebot.services import owner


def test_admin_id_optional_and_placeholder():
    assert Settings(_env_file=None, bot_token="1:x").admin_id is None  # type: ignore[call-arg]
    assert Settings(_env_file=None, bot_token="1:x", admin_id=123456789).admin_id is None  # type: ignore[call-arg]
    assert Settings(_env_file=None, bot_token="1:x", admin_id=42).admin_id == 42  # type: ignore[call-arg]


def test_save_load_private(tmp_path):
    assert owner.load(tmp_path) is None
    claim = owner.Claim(tmp_path)
    claim.publish()
    code_file = tmp_path / owner.CODE_FILE
    assert code_file.read_text().strip() == claim.code
    assert stat.S_IMODE(code_file.stat().st_mode) == 0o600
    owner.save(tmp_path, 777)
    assert owner.load(tmp_path) == 777
    assert not code_file.exists()
    assert stat.S_IMODE((tmp_path / owner.FILE_NAME).stat().st_mode) == 0o600


def test_broken_file_is_none(tmp_path):
    (tmp_path / owner.FILE_NAME).write_text("{oops")
    assert owner.load(tmp_path) is None
    (tmp_path / owner.FILE_NAME).write_text('{"owner_id": -3}')
    assert owner.load(tmp_path) is None


def test_claim_check(tmp_path):
    claim = owner.Claim(tmp_path, code="123456")
    assert claim.check(1, "привет") == "no_code"
    assert claim.check(1, "/start") == "no_code"
    assert claim.check(1, "/start 000000") == "wrong"
    assert claim.check(1, "/start@uni_bot 123456") == "ok"


def test_claim_limits(tmp_path):
    claim = owner.Claim(tmp_path, code="123456")
    for _ in range(owner.MAX_TRIES_PER_USER):
        assert claim.check(5, "/start 1") == "wrong"
    assert claim.check(5, "/start 123456") == "blocked"
    for uid in range(100, 100 + owner.MAX_TRIES_TOTAL):
        claim.check(uid, "/start 1")
    assert claim.code != "123456" or claim.total == 0  # перебор — код сменился
    assert (tmp_path / owner.CODE_FILE).read_text().strip() == claim.code


def test_codes_are_six_digits():
    codes = {owner.new_code() for _ in range(50)}
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    assert len(codes) > 1


def test_wait_owner_flow(tmp_path):
    import asyncio
    from types import SimpleNamespace

    try:
        from homebot.owner_claim import wait_owner
    except ImportError:  # нет aiogram
        return

    def upd(uid, user, text, chat="private"):
        msg = SimpleNamespace(chat=SimpleNamespace(id=user, type=chat), from_user=SimpleNamespace(id=user), text=text)
        return SimpleNamespace(update_id=uid, message=msg)

    class FakeBot:
        def __init__(self):
            self.sent = []
            self.batches = None

        async def delete_webhook(self, **_):
            return True

        async def get_updates(self, **_):
            if self.batches is None:
                code = (tmp_path / owner.CODE_FILE).read_text().strip()
                self.batches = [[upd(1, 9, "/start"), upd(2, 9, "/start 1"), upd(3, 7, f"/start {code}", chat="group")],
                                [upd(4, 8, f"/start {code}")]]  # fmt: skip
            return self.batches.pop(0) if self.batches else []

        async def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))

    bot = FakeBot()
    assert asyncio.run(wait_owner(bot, tmp_path)) == 8  # type: ignore[arg-type]
    assert owner.load(tmp_path) == 8
    assert [c for c, _ in bot.sent] == [9, 9, 8]  # из группы код не принимается
