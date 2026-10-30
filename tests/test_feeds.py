from homebot.services.feeds import FeedParseError, parse_feed

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Тест &amp; Ко</title>
  <item><title>Первая &lt;новость&gt;</title><link>https://example.com/1</link></item>
  <item><title>  Вторая
      новость  </title><link>javascript:alert(1)</link></item>
  <item><title></title><link>https://example.com/empty</link></item>
  <item><title>Третья</title></item>
  <item><title>Четвёртая</title><link>https://example.com/4</link></item>
</channel></rss>""".encode()

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom-лента</title>
  <entry><title>Запись 1</title>
    <link rel="self" href="https://example.com/self"/>
    <link rel="alternate" href="https://example.com/a1"/></entry>
  <entry><title>Запись 2</title><link href="https://example.com/a2"/></entry>
</feed>""".encode()

RDF = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/">
  <channel><title>RSS 1.0</title></channel>
  <item><title>Старый формат</title><link>https://example.com/rdf</link></item>
</rdf:RDF>""".encode()


def test_rss_basic():
    title, items = parse_feed(RSS, limit=10)
    assert title == "Тест & Ко"
    assert [i.title for i in items] == ["Первая <новость>", "Вторая новость", "Третья", "Четвёртая"]
    assert items[0].link == "https://example.com/1"


def test_unsafe_links_are_dropped():
    _, items = parse_feed(RSS, limit=10)
    assert items[1].link is None  # javascript: не пропускаем
    assert items[2].link is None  # ссылки нет вовсе


def test_limit_is_respected():
    _, items = parse_feed(RSS, limit=2)
    assert len(items) == 2


def test_atom_prefers_alternate_link():
    title, items = parse_feed(ATOM, limit=5)
    assert title == "Atom-лента"
    assert items[0].link == "https://example.com/a1"
    assert items[1].link == "https://example.com/a2"  # rel по умолчанию = alternate


def test_rss1_rdf():
    title, items = parse_feed(RDF, limit=5)
    assert title == "RSS 1.0"
    assert [i.title for i in items] == ["Старый формат"]


def test_garbage_raises_feed_parse_error():
    for raw in (b"", b"<html><body>not a feed</body></html>", b"<rss><channel><item>"):
        try:
            parse_feed(raw, limit=3)
        except FeedParseError:
            continue
        raise AssertionError(f"FeedParseError expected for {raw!r}")


def test_xml_bombs_and_external_entities_are_rejected():
    billion_laughs = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<rss><channel><title>&lol2;</title></channel></rss>"""
    xxe = b"""<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<rss><channel><title>&xxe;</title></channel></rss>"""
    for payload in (billion_laughs, xxe):
        try:
            parse_feed(payload, limit=3)
        except FeedParseError:
            continue
        raise AssertionError("entity declarations must be rejected")
