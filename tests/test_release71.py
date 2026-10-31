"""7.1: подпись релизов и проверка новых версий на GitHub."""

from __future__ import annotations

import asyncio
import base64
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from homebot.services import releases, signing

RAW = serialization.Encoding.Raw


def _key():
    key = Ed25519PrivateKey.generate()
    return key, key.public_key().public_bytes(RAW, serialization.PublicFormat.Raw)


def test_sign_verify(tmp_path):
    key, pub = _key()
    archive = tmp_path / "homebot.tar.gz"
    archive.write_bytes(b"data")
    digest = hashlib.sha256(b"data").hexdigest()
    assert signing.status(archive, digest, []) == signing.NO_KEYS
    assert signing.status(archive, digest, [pub]) == signing.UNSIGNED
    signing.sig_path(archive).write_text(base64.b64encode(key.sign(signing.message(digest))).decode())
    assert signing.status(archive, digest, [pub]) == signing.SIGNED
    _, other_pub = _key()
    assert signing.status(archive, digest, [other_pub]) == signing.BAD
    assert signing.status(archive, "0" * 64, [pub]) == signing.BAD
    signing.sig_path(archive).write_text("not base64!")
    assert signing.status(archive, digest, [pub]) == signing.BAD


def test_keys_file(tmp_path):
    _, pub = _key()
    f = tmp_path / "keys.txt"
    f.write_text(f"# comment\n{base64.b64encode(pub).decode()}  # me\nbroken\n" + base64.b64encode(b"short").decode())
    assert signing.load_keys(f) == [pub]
    assert signing.load_keys(tmp_path / "none.txt") == []
    assert isinstance(signing.load_keys(), list)  # файл в репозитории читается


def _gh(version, pre=False, sig=True, draft=False):
    name = f"homebot-{version}.tar.gz"
    assets = [{"name": name, "browser_download_url": f"https://gh.test/{name}"}]
    if sig:
        assets.append({"name": name + ".sig", "browser_download_url": f"https://gh.test/{name}.sig"})
    return {"prerelease": pre, "draft": draft, "html_url": f"https://gh.test/v{version}", "assets": assets}


def test_parse_and_newest():
    data = [_gh("7.2.0", pre=True), _gh("7.1.0", sig=False), _gh("9.0.0", draft=True), {"assets": [{"name": "x.zip"}]}, "junk"]
    rels = releases.parse(data)
    assert [r.version for r in rels] == ["7.2.0", "7.1.0"]
    assert rels[1].sig_url == "" and rels[0].sig_url.endswith(".sig")
    assert releases.newest(rels, "7.0.0", "stable").version == "7.1.0"  # type: ignore[union-attr]
    assert releases.newest(rels, "7.0.0", "beta").version == "7.2.0"  # type: ignore[union-attr]
    assert releases.newest(rels, "7.1.0", "stable") is None
    assert releases.parse({"message": "rate limit"}) == []


def test_watch_notifies_once(monkeypatch):
    from homebot.jobs import release_watch

    sent = []

    class Bot:
        async def send_message(self, chat, text, reply_markup=None):
            sent.append(text)

    class Store(dict):
        def get(self, k, d=None):
            return super().get(k, d)

        async def set(self, k, v):
            self[k] = v

    async def fake_fetch(http, repo):
        return releases.parse([_gh("99.0.0")])

    monkeypatch.setattr(releases, "fetch", fake_fetch)
    job = release_watch.ReleaseWatchJob(bot=Bot(), admin_id=1, store=Store(), http=None, repo="a/b", channel="stable")
    assert asyncio.run(job.tick()).version == "99.0.0"  # type: ignore[union-attr]
    assert asyncio.run(job.tick()) is None
    assert len(sent) == 1 and "99.0.0" in sent[0]
