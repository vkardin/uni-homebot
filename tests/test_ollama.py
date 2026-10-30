import asyncio
import tempfile
from pathlib import Path

import aiohttp

from homebot.services.ollama import (
    OllamaAPIError,
    OllamaService,
    OllamaTimeout,
    OllamaUnavailable,
    normalize_keep_alive,
    strip_think,
)
from homebot.services.state import StateStore
from tests.fakes import FakeResponse, FakeSession


def _service(handler, tmp: str, **overrides) -> tuple[OllamaService, FakeSession, StateStore]:
    session = FakeSession(handler)
    store = StateStore(Path(tmp) / "state.json")
    store.load()
    params = {
        "host": "http://ollama.test:11434/",
        "default_model": "llama3.2",
        "request_timeout": 30,
        "keep_alive": "5m",
        "max_history": 4,
        "system_prompt": "SYS",
    }
    params.update(overrides)
    return OllamaService(session, store, **params), session, store  # type: ignore[arg-type]


def test_normalize_keep_alive():
    assert normalize_keep_alive("5m") == "5m"
    assert normalize_keep_alive(" 30s ") == "30s"
    assert normalize_keep_alive("0") == 0
    assert normalize_keep_alive("-1") == -1


def test_strip_think():
    assert strip_think("<think>рассуждаю\nдолго</think>\n\nОтвет") == "Ответ"
    assert strip_think("без рассуждений") == "без рассуждений"


def test_chat_payload_and_response():
    with tempfile.TemporaryDirectory() as tmp:

        def handler(method, url, kwargs):
            return FakeResponse(body={"message": {"role": "assistant", "content": "<think>x</think>Привет!"}})

        service, session, _ = _service(handler, tmp)
        history = [{"role": "user", "content": f"q{i}"} for i in range(10)]
        reply = asyncio.run(service.chat(history))

        assert reply == "Привет!"
        method, url, kwargs = session.calls[0]
        assert (method, url) == ("POST", "http://ollama.test:11434/api/chat")  # слэш в хосте не дублируется
        payload = kwargs["json"]
        assert payload["model"] == "llama3.2"
        assert payload["stream"] is False
        assert payload["keep_alive"] == "5m"
        assert payload["messages"][0] == {"role": "system", "content": "SYS"}
        assert [m["content"] for m in payload["messages"][1:]] == ["q6", "q7", "q8", "q9"]  # max_history=4


def test_selected_model_overrides_default_and_persists():
    with tempfile.TemporaryDirectory() as tmp:

        def handler(method, url, kwargs):
            return FakeResponse(body={"message": {"content": "ok"}})

        service, session, _ = _service(handler, tmp)
        asyncio.run(service.set_active_model("qwen3:8b"))
        asyncio.run(service.chat([{"role": "user", "content": "hi"}]))
        assert session.calls[0][2]["json"]["model"] == "qwen3:8b"

        restarted, _, _ = _service(handler, tmp)  # «перезапуск бота»
        assert restarted.active_model == "qwen3:8b"


def test_unload_all_loaded_models_uses_keep_alive_zero():
    with tempfile.TemporaryDirectory() as tmp:

        def handler(method, url, kwargs):
            if url.endswith("/api/ps"):
                return FakeResponse(
                    body={
                        "models": [
                            {"name": "llama3.2:latest", "size": 1, "size_vram": 0},
                            {"name": "qwen3:8b", "size": 2, "size_vram": 1},
                        ]
                    }
                )
            return FakeResponse(body={"done": True})

        service, session, _ = _service(handler, tmp)
        assert asyncio.run(service.unload()) == ["llama3.2:latest", "qwen3:8b"]

        generate_calls = [c for c in session.calls if c[1].endswith("/api/generate")]
        assert [c[2]["json"] for c in generate_calls] == [
            {"model": "llama3.2:latest", "keep_alive": 0},
            {"model": "qwen3:8b", "keep_alive": 0},
        ]  # ровно keep_alive: 0 (число, не строка) и без prompt


def test_unload_with_nothing_loaded_makes_no_generate_call():
    with tempfile.TemporaryDirectory() as tmp:
        service, session, _ = _service(lambda *a: FakeResponse(body={"models": []}), tmp)
        assert asyncio.run(service.unload()) == []
        assert all(not c[1].endswith("/api/generate") for c in session.calls)


def test_loaded_models_parsing_tolerates_junk():
    with tempfile.TemporaryDirectory() as tmp:
        body = {
            "models": [
                {"name": "a", "size": 10, "size_vram": 5, "expires_at": "2026-10-13T09:00:00+03:00"},
                {"name": "b", "expires_at": "garbage"},
                "junk",
                {"no_name": 1},
            ]
        }
        service, _, _ = _service(lambda *a: FakeResponse(body=body), tmp)
        models = asyncio.run(service.loaded_models())
        assert [m.name for m in models] == ["a", "b"]
        assert models[0].expires_at is not None and models[1].expires_at is None


def test_http_error_message_is_extracted():
    with tempfile.TemporaryDirectory() as tmp:
        handler = lambda *a: FakeResponse(status=404, body={"error": "model 'nope' not found"})  # noqa: E731
        service, _, _ = _service(handler, tmp)
        try:
            asyncio.run(service.chat([{"role": "user", "content": "hi"}]))
        except OllamaAPIError as exc:
            assert "404" in str(exc) and "not found" in str(exc)
        else:
            raise AssertionError("OllamaAPIError expected")


def test_network_failures_are_mapped():
    with tempfile.TemporaryDirectory() as tmp:
        cases = [
            (aiohttp.ClientConnectionError("refused"), OllamaUnavailable),
            (TimeoutError(), OllamaTimeout),
            (aiohttp.ClientError("weird"), OllamaAPIError),
        ]
        for raised, expected in cases:

            def handler(method, url, kwargs, raised=raised):
                raise raised

            service, _, _ = _service(handler, tmp)
            try:
                asyncio.run(service.list_models())
            except expected:
                continue
            raise AssertionError(f"{type(raised).__name__} should map to {expected.__name__}")


def test_bad_json_and_bad_shape_are_api_errors():
    with tempfile.TemporaryDirectory() as tmp:
        service, _, _ = _service(lambda *a: FakeResponse(raw=b"<html>oops</html>"), tmp)
        for call in (service.list_models(), service.chat([{"role": "user", "content": "x"}])):
            try:
                asyncio.run(call)
            except OllamaAPIError:
                continue
            raise AssertionError("OllamaAPIError expected")

        service, _, _ = _service(lambda *a: FakeResponse(body={"unexpected": True}), tmp)
        try:
            asyncio.run(service.chat([{"role": "user", "content": "x"}]))
        except OllamaAPIError as exc:
            assert "формат" in str(exc)
        else:
            raise AssertionError("OllamaAPIError expected")


def test_summarize_truncates_huge_input_and_picks_prompt():
    with tempfile.TemporaryDirectory() as tmp:
        handler = lambda *a: FakeResponse(body={"message": {"content": "- пункт"}})  # noqa: E731
        service, session, _ = _service(handler, tmp)
        asyncio.run(service.summarize("я" * 50_000, kind="news"))
        messages = session.calls[0][2]["json"]["messages"]
        assert "новост" in messages[0]["content"].lower()
        assert len(messages[1]["content"]) < 12_100 and messages[1]["content"].endswith("[текст обрезан]")


def test_generations_are_serialized():
    with tempfile.TemporaryDirectory() as tmp:
        running = 0
        peak = 0

        class SlowResponse(FakeResponse):
            async def __aenter__(self):
                nonlocal running, peak
                running += 1
                peak = max(peak, running)
                await asyncio.sleep(0.02)
                return self

            async def __aexit__(self, *exc_info):
                nonlocal running
                running -= 1
                return False

        service, _, _ = _service(lambda *a: SlowResponse(body={"message": {"content": "ok"}}), tmp)

        async def scenario():
            await asyncio.gather(*(service.chat([{"role": "user", "content": str(i)}]) for i in range(4)))

        asyncio.run(scenario())
        assert peak == 1  # две тяжёлые генерации одновременно на домашнем сервере не нужны
