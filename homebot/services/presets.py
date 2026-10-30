"""Готовые подборки RSS-лент по темам. Каждая лента проверяется при добавлении — нерабочие не попадут в список."""

from __future__ import annotations

# ключ -> (подпись, [ленты])
PRESETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "ua": ("🇺🇦 Україна", ("https://www.pravda.com.ua/rss/",)),
    "tech": (
        "💻 Технологии",
        (
            "https://www.theverge.com/rss/index.xml",
            "https://feeds.arstechnica.com/arstechnica/index",
            "https://itc.ua/feed/",
        ),
    ),
    "science": (
        "🔬 Наука",
        ("https://www.sciencedaily.com/rss/top/science.xml", "https://www.newscientist.com/feed/home/"),
    ),
    "games": ("🎮 Игры", ("https://www.pcgamer.com/rss/", "https://www.gamespot.com/feeds/news/")),
    "movies": ("🎬 Кино и сериалы", ("https://deadline.com/feed/", "https://www.indiewire.com/feed/")),
    "music": ("🎵 Музыка", ("https://www.stereogum.com/feed/", "https://consequence.net/feed/")),
}
PRESET_KEYS = tuple(PRESETS)
