"""Разбор RSS 2.0 / RSS 1.0 (RDF) / Atom. Чистая логика, без сетевого ввода-вывода.

Для каждой новости достаём: заголовок, ссылку, дату публикации, краткое описание и устойчивый
идентификатор (guid/id/ссылка) — по нему бот понимает, что новость уже показывалась.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

MAX_TITLE_LEN = 200
MAX_SUMMARY_LEN = 1200

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
# Хвосты WordPress и подобных CMS, которые не несут смысла: «The post X appeared first on Y.»
_BOILERPLATE_RE = re.compile(
    r"(The post .{0,300}? appeared first on .{0,100}?\.?$)|(Continue reading.*$)|(Read more.*$)|(\[…\]|\[\.\.\.\])\s*$",
    re.IGNORECASE,
)


class FeedParseError(ValueError):
    """Лента не разобрана (битый XML, неизвестный формат)."""


def truncate_feed(data: bytes) -> bytes | None:
    """Огромная лента (десятки МБ) → её начало (свежие записи сверху), закрытое так, чтобы XML разобрался. None — не вышло."""
    head = data[:4096].lower()
    for end, tail in ((b"</item>", b"</channel></rss>"), (b"</entry>", b"</feed>")):
        if (tail == b"</feed>" and b"<feed" not in head) or (tail != b"</feed>" and b"<rss" not in head):
            continue
        cut = data.rfind(end)
        if cut > 0:
            return data[: cut + len(end)] + tail
    return None


@dataclass(frozen=True, slots=True)
class NewsItem:
    title: str
    link: str | None = None
    published: datetime | None = None  # всегда в UTC
    summary: str = ""
    uid: str = ""

    @property
    def key(self) -> str:
        """Ключ для «уже видели»: guid/id, иначе ссылка, иначе хэш заголовка."""
        base = self.uid or self.link or self.title
        return hashlib.sha1(base.encode()).hexdigest()


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text(element: Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _clean_title(raw: str) -> str:
    title = html.unescape(raw).strip()
    return title if len(title) <= MAX_TITLE_LEN else title[: MAX_TITLE_LEN - 1].rstrip() + "…"


def clean_summary(raw: str) -> str:
    """HTML → короткий текст без разметки и служебных хвостов."""
    text = html.unescape(_TAG_RE.sub(" ", html.unescape(raw)))
    text = _SPACE_RE.sub(" ", text).strip()
    text = _BOILERPLATE_RE.sub("", text).strip()
    if len(text) > MAX_SUMMARY_LEN:
        text = text[:MAX_SUMMARY_LEN].rsplit(" ", 1)[0] + "…"
    return text


def _safe_link(raw: str | None) -> str | None:
    if raw is None:
        return None
    link = raw.strip()
    return link if link.startswith(("http://", "https://")) else None


def parse_date(raw: str) -> datetime | None:
    """RFC 822 (RSS pubDate) или ISO 8601 (Atom, dc:date) → datetime в UTC."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _first_text(element: Element, *names: str) -> str:
    for name in names:
        found = element.find(f"{{*}}{name}")
        if found is None:
            found = element.find(name)
        text = _text(found)
        if text:
            return text
    return ""


def parse_feed(raw: bytes, limit: int) -> tuple[str | None, list[NewsItem]]:
    """Возвращает `(название_ленты, [новости])`, не более `limit` новостей."""
    try:
        root = SafeET.fromstring(raw)
    except (ParseError, DefusedXmlException) as exc:
        raise FeedParseError(f"некорректный XML: {exc}") from exc

    items: list[NewsItem] = []

    if _local(root.tag) == "feed":  # Atom
        title = _text(root.find("{*}title")) or None
        for entry in root.findall("{*}entry"):
            entry_title = _clean_title(_text(entry.find("{*}title")))
            if not entry_title:
                continue
            link = None
            for link_el in entry.findall("{*}link"):
                if link_el.get("rel", "alternate") == "alternate":
                    link = _safe_link(link_el.get("href"))
                    if link:
                        break
            items.append(
                NewsItem(
                    entry_title,
                    link,
                    published=parse_date(_first_text(entry, "published", "updated", "date")),
                    summary=clean_summary(_first_text(entry, "summary", "content")),
                    uid=_first_text(entry, "id"),
                )
            )
            if len(items) >= limit:
                break
        return title, items

    if _local(root.tag) not in {"rss", "RDF"}:
        raise FeedParseError(f"неизвестный формат ленты: <{_local(root.tag) or '?'}>")

    channel = root.find("channel")
    if channel is None:
        channel = root.find("{*}channel")
    title = _text(channel.find("{*}title")) if channel is not None else ""

    for element in root.iter():  # у RSS 1.0 <item> лежат рядом с <channel>, а не внутри
        if _local(element.tag) != "item":
            continue
        item_title = _clean_title(_text(element.find("{*}title")))
        if not item_title:
            continue
        items.append(
            NewsItem(
                item_title,
                _safe_link(_text(element.find("{*}link"))),
                published=parse_date(_first_text(element, "pubDate", "date", "published", "updated")),
                summary=clean_summary(_first_text(element, "description", "encoded", "summary")),
                uid=_first_text(element, "guid"),
            )
        )
        if len(items) >= limit:
            break
    return (title or None), items
