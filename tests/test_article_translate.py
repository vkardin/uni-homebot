"""Переводчики текстов: локальная модель (Ollama) и LibreTranslate."""

import asyncio


def test_ollama_article_translator_chunks_and_retries_after_preemption():
    from homebot.services.article_translate import OllamaArticleTranslator, chunks
    from homebot.services.ollama import OllamaPreempted

    assert chunks(["a" * 1000, "b" * 1000, "c" * 100], 1500) == [[0], [1, 2]]

    class FakeOllama:
        def __init__(self):
            self.calls = 0
            self.preempt = True

        async def ask(self, system, text):
            self.calls += 1
            if self.preempt:
                self.preempt = False
                raise OllamaPreempted("busy")
            lines = text.splitlines()
            return "\n".join(f"{n}. Русский {n}" if n != 2 else f"{n}. still english" for n in range(1, len(lines) + 1))

    async def scenario():
        import homebot.services.article_translate as module

        original_sleep = module.asyncio.sleep

        async def no_sleep(seconds):
            await original_sleep(0)

        module.asyncio.sleep = no_sleep
        try:
            ollama = FakeOllama()
            translator = OllamaArticleTranslator(ollama, max_chars=250)
            result = await translator(["x" * 100, "y" * 100, "z" * 100, "w" * 100])
        finally:
            module.asyncio.sleep = original_sleep
        assert result[0] == "Русский 1" and result[1] is None  # ответ не по-русски — оставляем оригинал
        assert result[3] is None  # за пределом max_chars не переводим
        assert ollama.calls == 2  # первая попытка прервана вашим запросом — повторили

    asyncio.run(scenario())


def test_libretranslate_client():
    from homebot.services.article_translate import LibreTranslator

    class Response:
        def __init__(self, payload):
            self.payload = payload

        async def json(self, content_type=None):
            return self.payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class Session:
        def __init__(self):
            self.bodies = []

        def post(self, url, json=None, timeout=None):
            self.bodies.append((url, json))
            return Response({"translatedText": [f"ru:{q}" for q in json["q"]]})

    async def scenario():
        session = Session()
        result = await LibreTranslator(session, "http://lt:5000/", "key")(["Hello", "World"])  # type: ignore[arg-type]
        assert result == ["ru:Hello", "ru:World"]
        url, body = session.bodies[0]
        assert url == "http://lt:5000/translate" and body["target"] == "ru" and body["api_key"] == "key"

    asyncio.run(scenario())
