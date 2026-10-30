import asyncio
import json
import tempfile
from pathlib import Path

from homebot.services.state import StateStore


def test_roundtrip_and_persistence():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sub" / "state.json"  # каталог создаётся автоматически
        store = StateStore(path)
        store.load()
        assert store.get("missing", 42) == 42

        asyncio.run(store.set("task.alerts.enabled", True))
        asyncio.run(store.set("ollama.model", "qwen3:8b"))

        reloaded = StateStore(path)
        reloaded.load()
        assert reloaded.get("task.alerts.enabled") is True
        assert reloaded.get("ollama.model") == "qwen3:8b"
        assert not path.with_suffix(".tmp").exists()  # временный файл не остаётся


def test_corrupt_file_is_quarantined_not_fatal():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        path.write_text("{ not json", encoding="utf-8")

        store = StateStore(path)
        store.load()  # не должно бросать исключение

        assert store.get("anything") is None
        assert path.with_suffix(".corrupt").exists()
        asyncio.run(store.set("k", 1))  # и после этого запись работает
        assert json.loads(path.read_text(encoding="utf-8")) == {"k": 1}


def test_non_object_root_is_treated_as_corrupt():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        store = StateStore(path)
        store.load()
        assert store.get("0") is None
        assert path.with_suffix(".corrupt").exists()


def test_write_failure_does_not_raise():
    with tempfile.TemporaryDirectory() as tmp:
        blocker = Path(tmp) / "file"
        blocker.write_text("x")
        store = StateStore(blocker / "state.json")  # родитель — файл, mkdir невозможен
        asyncio.run(store.set("k", "v"))  # ошибка логируется, бот не падает
        assert store.get("k") == "v"  # в памяти значение при этом сохранилось
