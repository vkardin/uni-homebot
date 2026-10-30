import asyncio
import io
import json
import tempfile
from pathlib import Path

from homebot.services.ollama import OllamaError, OllamaService, visible_text
from homebot.services.state import StateStore
from homebot.services.translate import parse_numbered
from homebot.services.webtext import WebTextError, document_to_text, extract_urls, html_to_text, pdf_to_text
from tests.fakes import FakeResponse, FakeSession


# ------------------------------------------------------------------- перевод
def test_parse_numbered():
    text = "Вот перевод:\n1. Первый\n2) «Второй»\n\n5. Лишний\n2. дубль\n3.   \n"
    assert parse_numbered(text, 3) == {0: "Первый", 1: "Второй"}


# ------------------------------------------------------------------- веб-текст
HTML = """<html><head><title> Статья &amp; тест </title><style>.x{}</style></head>
<body><nav>Меню Меню Меню</nav><header>Шапка</header>
<article><h1>Заголовок</h1><p>Первый абзац статьи, достаточно длинный, чтобы считаться основным текстом страницы.</p>
<p>Второй абзац с <b>жирным</b> словом и ещё немного текста для объёма, чтобы набрать нужную длину.</p>
<p>Третий абзац: ещё больше текста, потому что статья должна быть содержательной и полезной читателю.</p>
<script>alert('x')</script></article><footer>Подвал</footer></body></html>"""


def test_html_to_text_prefers_article():
    title, text = html_to_text(HTML)
    assert title == "Статья & тест"
    assert text.startswith("Заголовок\nПервый абзац")
    for junk in ("Меню", "Шапка", "Подвал", "alert"):
        assert junk not in text


def test_html_without_article_uses_body():
    _, text = html_to_text("<p>Просто текст</p><script>x()</script><p>второй</p>")
    assert text == "Просто текст\nвторой"


def test_extract_urls():
    assert extract_urls("глянь https://example.com/a?b=1, и (https://x.org/y).") == [
        "https://example.com/a?b=1",
        "https://x.org/y",
    ]
    assert extract_urls("без ссылок") == []


def _make_pdf(text: str) -> bytes:
    from matplotlib.backends.backend_pdf import FigureCanvasPdf
    from matplotlib.figure import Figure

    fig = Figure(figsize=(4, 2))
    FigureCanvasPdf(fig)
    fig.text(0.05, 0.5, text)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="pdf")
    return buffer.getvalue()


def test_pdf_and_documents():
    assert "Hello PDF world" in pdf_to_text(_make_pdf("Hello PDF world"))
    assert "Hello PDF world" in document_to_text(_make_pdf("Hello PDF world"), "doc.PDF", None)
    assert document_to_text("заметка\n".encode(), "notes.md", None) == "заметка"
    for data, name in ((b"not a pdf", "x.pdf"), (b"", "empty.txt"), (b"\x00\x01", "image.png")):
        try:
            document_to_text(data, name, None)
        except WebTextError:
            continue
        raise AssertionError(f"WebTextError expected for {name}")


# ------------------------------------------------------------- потоковый Ollama
def _ollama(handler, tmp: str) -> OllamaService:
    store = StateStore(Path(tmp) / "s.json")
    return OllamaService(
        FakeSession(handler), store, host="http://o", default_model="m", request_timeout=30,
        keep_alive="5m", max_history=4, system_prompt="SYS",
    )  # type: ignore[arg-type]  # fmt: skip


def _stream(*chunks: dict) -> bytes:
    return b"".join(json.dumps(c, ensure_ascii=False).encode() + b"\n" for c in chunks)


def test_chat_stream_yields_pieces_and_releases_lock():
    with tempfile.TemporaryDirectory() as tmp:
        body = _stream(
            {"message": {"content": "<think>хм"}}, {"message": {"content": "</think>При"}},
            {"message": {"content": "вет!"}}, {"done": True}, {"message": {"content": "после done не читаем"}},
        )  # fmt: skip
        seen = []

        def handler(method, url, kwargs):
            seen.append((method, url, kwargs["json"]["stream"]))
            return FakeResponse(raw=body)

        service = _ollama(handler, tmp)

        async def scenario():
            pieces = [p async for p in service.chat_stream([{"role": "user", "content": "hi"}])]
            assert "".join(pieces) == "<think>хм</think>Привет!"
            assert not service.gate.holder is not None  # блокировка генерации отпущена

        asyncio.run(scenario())
        assert seen[0] == ("POST", "http://o/api/chat", True)


def test_chat_stream_error_inside_stream():
    with tempfile.TemporaryDirectory() as tmp:
        service = _ollama(lambda *a: FakeResponse(raw=_stream({"error": "model 'x' not found"})), tmp)

        async def scenario():
            async for _ in service.chat_stream([{"role": "user", "content": "hi"}]):
                pass

        try:
            asyncio.run(scenario())
        except OllamaError as exc:
            assert "not found" in str(exc)
        else:
            raise AssertionError("OllamaError expected")


def test_visible_text_hides_reasoning():
    assert visible_text("<think>долго думаю") == ("", True)
    assert visible_text("Начало <think>ещё") == ("Начало", True)
    assert visible_text("<think>a</think> Ответ") == ("Ответ", False)


def test_ask_uses_custom_system_prompt():
    with tempfile.TemporaryDirectory() as tmp:
        captured = {}

        def handler(method, url, kwargs):
            captured.update(kwargs["json"])
            return FakeResponse(body={"message": {"content": "ok"}})

        assert asyncio.run(_ollama(handler, tmp).ask("ПЕРЕВОДЧИК", "hello")) == "ok"
        assert captured["messages"][0] == {"role": "system", "content": "ПЕРЕВОДЧИК"}
        assert captured["stream"] is False


# ------------------------------------------------------------------ think=false
def _ollama_think(handler, tmp: str, think) -> OllamaService:
    return OllamaService(
        FakeSession(handler), StateStore(Path(tmp) / "s.json"), host="http://o", default_model="m",
        request_timeout=30, keep_alive="5m", max_history=4, system_prompt="SYS", think=think,
    )  # type: ignore[arg-type]  # fmt: skip


def test_think_false_is_sent_and_none_is_omitted():
    with tempfile.TemporaryDirectory() as tmp:
        payloads = []

        def handler(method, url, kwargs):
            payloads.append(dict(kwargs["json"]))
            return FakeResponse(body={"message": {"content": "ok"}})

        asyncio.run(_ollama_think(handler, tmp, False).ask("s", "x"))
        asyncio.run(_ollama_think(handler, tmp, None).ask("s", "x"))
        assert payloads[0]["think"] is False
        assert "think" not in payloads[1]


def test_model_without_thinking_support_is_retried_and_remembered():
    with tempfile.TemporaryDirectory() as tmp:
        payloads = []

        def handler(method, url, kwargs):
            payloads.append(dict(kwargs["json"]))
            if "think" in kwargs["json"]:
                return FakeResponse(status=400, body={"error": '"m" does not support thinking'})
            if kwargs["json"]["stream"]:
                return FakeResponse(raw=_stream({"message": {"content": "по"}}, {"message": {"content": "ток"}}, {"done": True}))
            return FakeResponse(body={"message": {"content": "ответ"}})

        service = _ollama_think(handler, tmp, False)
        assert asyncio.run(service.ask("s", "x")) == "ответ"
        assert [("think" in p) for p in payloads] == [True, False]

        async def stream():
            return "".join([p async for p in service.chat_stream([{"role": "user", "content": "hi"}])])

        assert asyncio.run(stream()) == "поток"
        assert "think" not in payloads[-1]  # модель запомнена: think больше не отправляем
        assert len(payloads) == 3


def test_stream_retry_without_think_on_first_request():
    with tempfile.TemporaryDirectory() as tmp:
        payloads = []

        def handler(method, url, kwargs):
            payloads.append(dict(kwargs["json"]))
            if "think" in kwargs["json"]:
                return FakeResponse(status=400, body={"error": "model does not support thinking"})
            return FakeResponse(raw=_stream({"message": {"content": "ok"}}, {"done": True}))

        service = _ollama_think(handler, tmp, False)

        async def stream():
            return "".join([p async for p in service.chat_stream([{"role": "user", "content": "hi"}])])

        assert asyncio.run(stream()) == "ok"
        assert [("think" in p) for p in payloads] == [True, False]
        assert not service.gate.holder is not None
