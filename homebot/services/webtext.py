"""Текст из ссылки, HTML-страницы, PDF или текстового файла — для резюме моделью."""

from __future__ import annotations

import io
import re
from html.parser import HTMLParser

import aiohttp

MAX_DOWNLOAD = 15 * 1024 * 1024
MAX_TEXT = 12_000
URL_RE = re.compile(r"https?://[^\s<>\"']+")

_SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "button", "iframe"}
_BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "blockquote"}
_TEXT_EXT = (".txt", ".md", ".log", ".csv", ".json", ".yaml", ".yml", ".ini", ".conf", ".py", ".sh")


class WebTextError(Exception):
    """Не удалось получить текст. Сообщение безопасно показывать пользователю."""


def extract_urls(text: str) -> list[str]:
    return [u.rstrip(".,;:!?)»") for u in URL_RE.findall(text)]


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._in_title = False
        self._main_depth = 0
        self.title = ""
        self.all_parts: list[str] = []
        self.main_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("article", "main"):
            self._main_depth += 1
        if tag in _BLOCK_TAGS:
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("article", "main") and self._main_depth:
            self._main_depth -= 1
        if tag in _BLOCK_TAGS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self.all_parts.append(data)
        if self._main_depth:
            self.main_parts.append(data)

    def _newline(self) -> None:
        self.all_parts.append("\n")
        if self._main_depth:
            self.main_parts.append("\n")


def _normalize(parts: list[str]) -> str:
    lines = (" ".join(line.split()) for line in "".join(parts).splitlines())
    return "\n".join(line for line in lines if line)


def html_to_text(markup: str) -> tuple[str | None, str]:
    """(заголовок, основной текст). Предпочитает содержимое <article>/<main>, если его достаточно."""
    parser = _Extractor()
    parser.feed(markup)
    parser.close()
    main = _normalize(parser.main_parts)
    text = main if len(main) >= 300 else _normalize(parser.all_parts)
    title = " ".join(parser.title.split()) or None
    return title, text


def pdf_to_text(data: bytes, max_chars: int = MAX_TEXT) -> str:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - зависимость есть в requirements.txt
        raise WebTextError("для PDF нужен пакет pypdf") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise WebTextError("PDF защищён паролем")
        chunks: list[str] = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            chunks.append(text)
            total += len(text)
            if total >= max_chars:
                break
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise WebTextError("не удалось прочитать PDF") from exc
    text = _normalize(chunks)
    if not text:
        raise WebTextError("в PDF нет текстового слоя (скан?)")
    return text[:max_chars]


def document_to_text(data: bytes, filename: str, mime: str | None) -> str:
    name = filename.lower()
    if name.endswith(".pdf") or mime == "application/pdf":
        return pdf_to_text(data)
    if name.endswith(_TEXT_EXT) or (mime or "").startswith("text/"):
        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            raise WebTextError("файл пустой")
        return text[:MAX_TEXT]
    raise WebTextError("поддерживаются PDF и текстовые файлы")


async def fetch_text(session: aiohttp.ClientSession, url: str) -> tuple[str | None, str]:
    """(заголовок, текст) страницы или PDF по ссылке."""
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=25),
            headers={"Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5"},
        ) as resp:
            if resp.status >= 400:
                raise WebTextError(f"сайт ответил HTTP {resp.status}")
            content_type = (resp.headers.get("Content-Type") or "").lower()
            buffer = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                buffer += chunk
                if len(buffer) > MAX_DOWNLOAD:
                    raise WebTextError("страница слишком большая")
            charset = resp.charset or "utf-8"
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise WebTextError(f"не удалось открыть ссылку ({type(exc).__name__})") from exc

    data = bytes(buffer)
    if "pdf" in content_type or data[:5] == b"%PDF-":
        return None, pdf_to_text(data)
    if content_type and not any(t in content_type for t in ("html", "text", "xml")):
        raise WebTextError(f"неподдерживаемый тип содержимого: {content_type.split(';')[0]}")
    try:
        markup = data.decode(charset, errors="replace")
    except LookupError:
        markup = data.decode("utf-8", errors="replace")
    title, text = html_to_text(markup)
    if len(text) < 80:
        raise WebTextError("на странице почти нет текста (возможно, она рисуется JavaScript-ом)")
    return title, text[:MAX_TEXT]
