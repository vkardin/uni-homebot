"""🌐 LibreTranslate вместо модели: заголовки, описания, сводка."""

import asyncio
from datetime import UTC, datetime

from homebot.services.feeds import NewsItem
from homebot.services.news import Candidate, NewsService
from homebot.services.prefs import NewsPrefs


class Machine:
    def __init__(self, fail=False):
        self.calls: list[list[str]] = []
        self.fail = fail

    async def __call__(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            return [None] * len(texts)
        return [f"Русский перевод текста «{t[:5]}»" for t in texts]


class NoModel:
    async def ask(self, *args, **kwargs):
        raise AssertionError("модель не должна переводить, когда есть LibreTranslate")


def candidate(uid, title, summary):
    item = NewsItem(title=title, link=f"https://x.test/{uid}", published=datetime(2026, 10, 13, tzinfo=UTC), summary=summary, uid=uid)
    return Candidate(item, "feed", "src")


def test_news_without_retell_translated_by_machine_only_where_needed():
    async def scenario():
        machine = Machine()
        service = NewsService(None, None, NoModel(), translator=machine)  # type: ignore[arg-type]
        picked = [
            candidate("1", "Apple unveils Home Collection", "New cameras and locks for the smart home."),
            candidate("2", "Взрывы в Павлограде", "Погиб человек."),
            candidate("3", "Nvidia RTX 5070 review", ""),
        ]
        stories = await service.retell(picked, NewsPrefs(retell=False))
        assert stories[0].title == "Русский перевод текста «Apple»" and stories[0].text == "Русский перевод текста «New c»"
        assert stories[1].title == "Взрывы в Павлограде"  # по-русски — не трогаем
        assert stories[2].title == "Русский перевод текста «Nvidi»" and stories[2].text == ""
        assert machine.calls == [
            ["Apple unveils Home Collection", "Nvidia RTX 5070 review"],  # заголовки — одной пачкой и первыми
            ["New cameras and locks for the smart home."],  # потом описания тех, что без пересказа
        ]
        assert await service.translate_title("Steam Next Fest starts") == "Русский перевод текста «Steam»"

        broken = NewsService(None, None, NoModel(), translator=Machine(fail=True))  # type: ignore[arg-type]
        fallback = await broken.retell(picked[:1], NewsPrefs(retell=False))
        assert fallback[0].title == "Apple unveils Home Collection"  # переводчик молчит — оригинал

    asyncio.run(scenario())


def test_light_translate_model_is_used_for_all_llm_translation():
    from homebot.services.article_translate import OllamaArticleTranslator

    class Model:
        def __init__(self):
            self.models: list[str | None] = []

        async def ask(self, system, text, *, model=None, schema=None):
            self.models.append(model)
            return "\n".join(f"{n}. Русский перевод строки" for n in range(1, len(text.splitlines()) + 1))

    async def scenario():
        model = Model()
        service = NewsService(None, None, model, translate_model="qwen3:1.7b")  # type: ignore[arg-type]
        assert await service.translate_title("Apple unveils Home Collection")
        await OllamaArticleTranslator(model, model="qwen3:1.7b")(["First paragraph of the article."])
        assert model.models == ["qwen3:1.7b"] * 2
        plain = NewsService(None, None, model)  # type: ignore[arg-type]
        await plain.translate_title("Steam Next Fest starts")
        assert model.models[-1] is None  # без настройки — модель как раньше

    asyncio.run(scenario())
