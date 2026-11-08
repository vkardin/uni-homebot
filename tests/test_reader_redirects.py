import asyncio
from unittest.mock import patch

import pytest

from homebot.services.webtext import WebTextError, fetch_text
from tests.fakes import FakeResponse, FakeSession


def test_public_reader_rejects_redirect_to_private_network():
    session = FakeSession(lambda *_: FakeResponse(status=302, headers={"Location": "http://127.0.0.1/admin"}))

    async def guard(url):
        if url.startswith("http://127.0.0.1"):
            raise ValueError("private address")
        return url

    with patch("homebot.services.urlguard.public_http_url", side_effect=guard):
        with pytest.raises(WebTextError, match="private address"):
            asyncio.run(fetch_text(session, "https://example.org/article", public_only=True))
    assert len(session.calls) == 1
    assert session.calls[0][2]["allow_redirects"] is False


def test_public_reader_follows_validated_relative_redirect():
    def response(method, url, kwargs):
        if url.endswith("/old"):
            return FakeResponse(status=301, headers={"Location": "/new"})
        return FakeResponse(raw=("<p>" + "Article text. " * 12 + "</p>").encode())

    session = FakeSession(response)
    checked = []

    async def guard(url):
        checked.append(url)
        return url

    with patch("homebot.services.urlguard.public_http_url", side_effect=guard):
        _, text = asyncio.run(fetch_text(session, "https://example.org/old", public_only=True))
    assert checked == ["https://example.org/old", "https://example.org/new"]
    assert "Article text." in text


def test_public_reader_limits_redirect_loops():
    session = FakeSession(lambda *_: FakeResponse(status=302, headers={"Location": "/loop"}))

    async def guard(url):
        return url

    with patch("homebot.services.urlguard.public_http_url", side_effect=guard):
        with pytest.raises(WebTextError, match="перенаправлений"):
            asyncio.run(fetch_text(session, "https://example.org/loop", public_only=True))
    assert len(session.calls) == 6
