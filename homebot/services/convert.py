"""🧰 Конвертер файлов и скачивание по ссылке. Здесь только логика (без aiogram): экран и кнопки — в handlers/tools.py.

Принципы:
* что не установлено на сервере — не предлагается (`detect()`), а не падает с ошибкой;
* одна задача за раз, у каждой тайм-аут; тяжёлые программы запускаются с низким приоритетом (`nice`);
* результат больше лимита Telegram (50 МБ) не отправляется — человеку объясняется, что делать;
* пользовательские тексты ошибок — только `ConvertError` (остальное уходит в журнал).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import logging
import os
import re
import shutil
import signal
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_INPUT = 20 * 1024 * 1024  # больше бот скачать не может (лимит Bot API)
MAX_OUTPUT = 50 * 1024 * 1024  # больше Telegram не принимает от бота
MAX_MERGE_FILES = 20
MAX_MERGE_BYTES = 60 * 1024 * 1024
PDF_PAGES = 10  # страниц PDF → картинки / текст-распознавание за раз
MAX_PIXELS = 80_000_000
GIF_SECONDS = 12

IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff", "heic", "heif", "avif"}
VIDEO_EXT = {"mp4", "mov", "mkv", "avi", "webm", "m4v", "3gp", "wmv", "flv", "mpg", "mpeg"}
AUDIO_EXT = {"mp3", "wav", "flac", "ogg", "oga", "opus", "m4a", "aac", "wma", "amr", "aiff"}
DOC_EXT = {"doc", "docx", "odt", "rtf", "txt", "xls", "xlsx", "ods", "csv", "ppt", "pptx", "odp"}
KINDS = ("image", "pdf", "doc", "audio", "video")
KIND_TITLE = {"image": "🖼 Картинка", "pdf": "📕 PDF", "doc": "📄 Документ", "audio": "🎵 Аудио", "video": "🎬 Видео"}


class ConvertError(Exception):
    """Ошибка, текст которой можно показать человеку."""


@dataclass(frozen=True, slots=True)
class Caps:
    """Что умеет этот сервер."""

    pillow: bool = False
    heif: bool = False
    pypdf: bool = False
    ffmpeg: bool = False
    poppler: bool = False
    tesseract: bool = False
    office: bool = False
    ghostscript: bool = False
    ytdlp: bool = False


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def detect() -> Caps:
    return Caps(
        pillow=_has_module("PIL"),
        heif=_has_module("pillow_heif"),
        pypdf=_has_module("pypdf"),
        ffmpeg=bool(shutil.which("ffmpeg")),
        poppler=bool(shutil.which("pdftoppm")),
        tesseract=bool(shutil.which("tesseract")),
        office=bool(shutil.which("soffice") or shutil.which("libreoffice")),
        ghostscript=bool(shutil.which("gs")),
        ytdlp=_has_module("yt_dlp"),
    )


# ------------------------------------------------------------------------- виды файлов и операции
def kind_of(name: str, mime: str = "") -> str | None:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext == "pdf" or mime == "application/pdf":
        return "pdf"
    for kind, exts in (("image", IMAGE_EXT), ("video", VIDEO_EXT), ("audio", AUDIO_EXT), ("doc", DOC_EXT)):
        if ext in exts:
            return kind
    if not ext:  # имени нет — смотрим на тип
        for kind in ("image", "video", "audio"):
            if mime.startswith(kind + "/"):
                return kind
    return None


@dataclass(frozen=True, slots=True)
class Op:
    key: str
    label: str


def ops_for(kind: str, caps: Caps, name: str = "") -> list[Op]:
    """Что можно сделать с файлом такого вида на этом сервере."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    ops: list[Op] = []
    if kind == "image":
        heic = ext in ("heic", "heif")
        if caps.pillow and (caps.heif or not heic):
            ops += [Op("img_jpg", "→ JPG"), Op("img_png", "→ PNG"), Op("img_webp", "→ WebP"), Op("img_pdf", "→ PDF")]
            ops += [Op("img_small", "🗜 Сжать"), Op("img_rot", "↻ Повернуть")]
        if caps.tesseract and caps.pillow and (caps.heif or not heic):
            ops.append(Op("img_ocr", "🔤 Текст с фото"))
    elif kind == "pdf":
        if caps.pypdf:
            ops.append(Op("pdf_txt", "📄 Текст"))
        if caps.poppler:
            ops.append(Op("pdf_png", "🖼 Страницы → PNG"))
        if caps.ghostscript:
            ops.append(Op("pdf_small", "🗜 Сжать"))
    elif kind == "doc":
        if caps.office:
            ops.append(Op("doc_pdf", "→ PDF"))
    elif kind == "audio":
        if caps.ffmpeg:
            ops += [Op("aud_mp3", "→ MP3"), Op("aud_m4a", "→ M4A"), Op("aud_voice", "🎙 Голосовым"), Op("aud_small", "🗜 Сжать")]
    elif kind == "video":
        if caps.ffmpeg:
            ops += [Op("vid_mp3", "🎵 Звук → MP3"), Op("vid_small", "🗜 Сжать"), Op("vid_gif", "🎞 В GIF"), Op("vid_frame", "📷 Кадр")]
    return ops


def op_by_key(kind: str, caps: Caps, key: str, name: str = "") -> Op | None:
    return next((o for o in ops_for(kind, caps, name) if o.key == key), None)


def describe_caps(caps: Caps) -> list[tuple[bool, str, str]]:
    """(есть ли, что это, чего без этого не будет) — для /doctor и страницы «Инструменты»."""
    return [
        (caps.pillow, "Pillow", "картинки: JPG, PNG, WebP, PDF, сжатие"),
        (caps.heif, "pillow-heif", "фото с iPhone (HEIC)"),
        (caps.pypdf, "pypdf", "текст из PDF и склейка PDF"),
        (caps.poppler, "poppler-utils", "страницы PDF → картинки"),
        (caps.tesseract, "tesseract-ocr", "распознавание текста с фото и сканов"),
        (caps.ffmpeg, "ffmpeg", "аудио и видео"),
        (caps.office, "LibreOffice", "Word, Excel, PowerPoint → PDF"),
        (caps.ghostscript, "ghostscript", "сжатие PDF"),
        (caps.ytdlp, "yt-dlp", "скачивание по ссылке"),
    ]


_APT = (
    ("ffmpeg", "ffmpeg"),
    ("poppler", "poppler-utils"),
    ("tesseract", "tesseract-ocr tesseract-ocr-rus tesseract-ocr-ukr"),
    ("ghostscript", "ghostscript"),
    ("office", "libreoffice-writer libreoffice-calc libreoffice-impress"),
)


def install_hint(caps: Caps) -> str:
    """Команда установки недостающих программ (пустая строка, если всё на месте)."""
    packages = [pkg for attr, pkg in _APT if not getattr(caps, attr)]
    return ("sudo apt install " + " ".join(packages)) if packages else ""


# ------------------------------------------------------------------------- запуск программ
_TAIL = 400


async def _exec(*args: str, timeout: float, cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Запуск внешней программы: низкий приоритет, тайм-аут (с убийством всей группы процессов), хвост ошибок."""
    if shutil.which(args[0]) is None:
        raise ConvertError(f"на сервере нет программы {args[0]}")
    cmd = [*(["nice", "-n", "10"] if shutil.which("nice") else []), *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env={**os.environ, **(env or {})},
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise ConvertError(f"на сервере нет программы {args[0]}") from exc
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        raise ConvertError("слишком долго — остановил. Попробуйте файл поменьше") from None
    except asyncio.CancelledError:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        raise
    return proc.returncode or 0, err.decode(errors="replace")[-_TAIL:].strip()


def _fail(what: str, err: str) -> ConvertError:
    log.warning("convert: %s failed: %s", what, err)
    return ConvertError(f"{what}: не получилось (файл повреждён или формат не поддерживается)")


def _ffmpeg_base() -> list[str]:
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", "2"]


# ------------------------------------------------------------------------- результат
@dataclass(frozen=True, slots=True)
class Result:
    path: Path
    kind: str = "document"  # document | audio | voice | video | animation | photo
    caption: str = ""
    title: str = ""


def _stem(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1]  # только имя файла, без путей
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = re.sub(r"[^\w.\- ]+", "_", stem, flags=re.UNICODE).strip(" ._") or "file"
    return stem[:60]


def _check_size(path: Path) -> Path:
    if not path.exists() or path.stat().st_size == 0:
        raise ConvertError("получился пустой файл")
    if path.stat().st_size > MAX_OUTPUT:
        raise ConvertError("результат больше 50 МБ — Telegram такой не принимает от бота")
    return path


# ------------------------------------------------------------------------- картинки (Pillow)
_heif_ready = False


def _pil() -> Any:
    global _heif_ready
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    if not _heif_ready:
        _heif_ready = True
        if _has_module("pillow_heif"):
            import pillow_heif

            pillow_heif.register_heif_opener()
    return Image


def _load(src: Path) -> Any:
    from PIL import ImageOps

    image = _pil()
    try:
        im = image.open(src)
        im.load()
    except image.DecompressionBombError as exc:
        raise ConvertError("картинка слишком большая") from exc
    except Exception as exc:  # Pillow бросает много разных исключений на битых файлах
        raise _fail("Картинка", str(exc)) from exc
    return ImageOps.exif_transpose(im)


def _flat(im: Any) -> Any:
    """RGB без прозрачности (белый фон) — для JPG и PDF."""
    image = _pil()
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def _image_job(op: str, src: Path, dst: Path) -> None:
    im = _load(src)
    if op == "img_jpg":
        _flat(im).save(dst, "JPEG", quality=92, optimize=True)
    elif op == "img_png":
        im.save(dst, "PNG", optimize=True)
    elif op == "img_webp":
        im.save(dst, "WEBP", quality=90)
    elif op == "img_pdf":
        _flat(im).save(dst, "PDF", resolution=150.0)
    elif op == "img_small":
        im = _flat(im)
        im.thumbnail((2048, 2048))
        im.save(dst, "JPEG", quality=72, optimize=True, progressive=True)
    elif op == "img_rot":
        im.rotate(-90, expand=True).save(dst, "PNG")
    else:  # pragma: no cover
        raise ConvertError("неизвестная операция")


_IMG_EXT = {"img_jpg": "jpg", "img_png": "png", "img_webp": "webp", "img_pdf": "pdf", "img_small": "jpg", "img_rot": "png"}


_langs: str | None = None


async def _ocr_langs() -> str:
    """Какие из языков rus/ukr/eng установлены у tesseract (результат запоминаем)."""
    global _langs
    if _langs is None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "tesseract",
                "--list-langs",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), 15)
        except (OSError, TimeoutError) as exc:
            raise ConvertError("tesseract не отвечает") from exc
        have = set((out + err).decode(errors="replace").split())
        _langs = "+".join(lang for lang in ("rus", "ukr", "eng") if lang in have) or "eng"
    return _langs


async def _ocr(image_path: Path, dst_base: Path) -> str:
    langs = await _ocr_langs()
    code, err = await _exec("tesseract", str(image_path), str(dst_base), "-l", langs, timeout=120)
    txt = dst_base.with_suffix(".txt")
    if code or not txt.exists():
        raise _fail("Распознавание", err)
    return txt.read_text(encoding="utf-8", errors="replace").strip()


# ------------------------------------------------------------------------- PDF
def _pdf_text(src: Path) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(src))
        if reader.is_encrypted:
            raise ConvertError("PDF защищён паролем")
        parts = [(page.extract_text() or "").strip() for page in reader.pages[:200]]
    except ConvertError:
        raise
    except Exception as exc:
        raise _fail("PDF", str(exc)) from exc
    return "\n\n".join(p for p in parts if p)


def merge_pdf_sync(sources: Sequence[Path], dst: Path) -> None:
    """Склейка PDF и картинок в один PDF по порядку."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    tmp = dst.parent
    for i, path in enumerate(sources):
        try:
            if path.suffix.lower() == ".pdf":
                writer.append(str(path))
            else:
                page = tmp / f"_page{i}.pdf"
                _flat(_load(path)).save(page, "PDF", resolution=150.0)
                writer.append(str(page))
        except ConvertError:
            raise
        except Exception as exc:
            raise _fail(f"Файл {i + 1}", str(exc)) from exc
    with dst.open("wb") as fh:
        writer.write(fh)


def _natural(path: Path) -> list[Any]:
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", path.name)]


def _zip(files: Sequence[Path], dst: Path) -> Path:
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_STORED) as zf:
        for f in files:
            zf.write(f, f.name)
    return dst


# ------------------------------------------------------------------------- движок
class Converter:
    def __init__(self, state_dir: Path) -> None:
        self._home = state_dir / "tools-home"  # домашний каталог для LibreOffice (корень системы только для чтения)
        self._lock: asyncio.Lock | None = None
        self.caps = detect()

    def refresh(self) -> Caps:
        self.caps = detect()
        return self.caps

    @property
    def busy(self) -> bool:
        return self._lock is not None and self._lock.locked()

    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # ------------------------------------------------------------------ один файл
    async def run(self, op: str, src: Path, name: str, out_dir: Path) -> list[Result]:
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = _stem(name)
        if op in _IMG_EXT:
            dst = out_dir / f"{stem}.{_IMG_EXT[op]}"
            if op == "img_small" and src.stat().st_size < 40_000:
                raise ConvertError("файл и так маленький")
            await asyncio.to_thread(_image_job, op, src, dst)
            return [Result(_check_size(dst))]
        if op == "img_ocr":
            png = out_dir / "_ocr.png"
            await asyncio.to_thread(lambda: _flat(_load(src)).convert("L").save(png, "PNG"))
            return self._text_result(await _ocr(png, out_dir / "text"), stem, out_dir)
        if op == "pdf_txt":
            text = await asyncio.to_thread(_pdf_text, src)
            if not text and self.caps.poppler and self.caps.tesseract:
                text = await self._pdf_ocr(src, out_dir)
            if not text:
                raise ConvertError("в PDF нет текста" + ("" if self.caps.tesseract else " (это скан; для сканов нужен tesseract-ocr)"))
            return self._text_result(text, stem, out_dir)
        if op == "pdf_png":
            return await self._pdf_pages(src, stem, out_dir)
        if op == "pdf_small":
            return await self._pdf_small(src, stem, out_dir)
        if op == "doc_pdf":
            return await self._office(src, stem, out_dir)
        if op.startswith(("aud_", "vid_")):
            return await self._media(op, src, stem, out_dir)
        raise ConvertError("такой операции нет")

    @staticmethod
    def _text_result(text: str, stem: str, out_dir: Path) -> list[Result]:
        text = text.strip()
        if not text:
            raise ConvertError("текст не найден")
        dst = out_dir / f"{stem}.txt"
        dst.write_text(text, encoding="utf-8")
        return [Result(_check_size(dst), caption=f"Символов: {len(text)}")]

    async def _pdf_ocr(self, src: Path, out_dir: Path) -> str:
        base = out_dir / "scan"
        code, err = await _exec("pdftoppm", "-r", "150", "-gray", "-png", "-f", "1", "-l", "5", str(src), str(base), timeout=120)
        if code:
            raise _fail("PDF", err)
        chunks = [await _ocr(p, p.with_suffix("")) for p in sorted(out_dir.glob("scan-*.png"), key=_natural)]
        return "\n\n".join(c for c in chunks if c)

    async def _pdf_pages(self, src: Path, stem: str, out_dir: Path) -> list[Result]:
        code, err = await _exec(
            "pdftoppm", "-r", "110", "-png", "-f", "1", "-l", str(PDF_PAGES), str(src), str(out_dir / "page"), timeout=180
        )
        pages = sorted(out_dir.glob("page-*.png"), key=_natural)
        if code or not pages:
            raise _fail("PDF", err)
        if len(pages) <= 3:
            return [Result(_check_size(p.rename(out_dir / f"{stem}_{i + 1}.png"))) for i, p in enumerate(pages)]
        archive = _zip(pages, out_dir / f"{stem}_pages.zip")
        return [Result(_check_size(archive), caption=f"Страниц: {len(pages)} (первые {PDF_PAGES}, если в файле больше)")]

    async def _pdf_small(self, src: Path, stem: str, out_dir: Path) -> list[Result]:
        dst = out_dir / f"{stem}_small.pdf"
        code, err = await _exec(
            "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4", "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dBATCH", "-dQUIET",
            f"-sOutputFile={dst}", str(src), timeout=240,
        )  # fmt: skip
        if code or not dst.exists():
            raise _fail("PDF", err)
        if dst.stat().st_size >= src.stat().st_size:
            raise ConvertError("сильнее сжать не получилось — файл и так компактный")
        return [Result(_check_size(dst), caption=f"Было {src.stat().st_size // 1024} КБ → стало {dst.stat().st_size // 1024} КБ")]

    async def _office(self, src: Path, stem: str, out_dir: Path) -> list[Result]:
        exe = shutil.which("soffice") or shutil.which("libreoffice") or "soffice"
        self._home.mkdir(parents=True, exist_ok=True)
        work = out_dir / "office"
        work.mkdir(exist_ok=True)
        local = work / f"in{src.suffix or '.txt'}"
        shutil.copyfile(src, local)
        code, err = await _exec(
            exe, "--headless", "--norestore", "--nolockcheck", "--nodefault",
            f"-env:UserInstallation=file://{self._home}/profile", "--convert-to", "pdf", "--outdir", str(work), str(local),
            timeout=240, env={"HOME": str(self._home)},
        )  # fmt: skip
        pdf = work / "in.pdf"
        if code or not pdf.exists():
            raise _fail("Документ", err)
        dst = out_dir / f"{stem}.pdf"
        pdf.rename(dst)
        return [Result(_check_size(dst))]

    # ------------------------------------------------------------------ аудио и видео
    async def _media(self, op: str, src: Path, stem: str, out_dir: Path) -> list[Result]:
        base = _ffmpeg_base()
        results: list[Result]
        if op in ("aud_mp3", "vid_mp3"):
            dst = out_dir / f"{stem}.mp3"
            args = [*base, "-i", str(src), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(dst)]
            results = [Result(dst, "audio", title=stem)]
        elif op == "aud_m4a":
            dst = out_dir / f"{stem}.m4a"
            args = [*base, "-i", str(src), "-vn", "-c:a", "aac", "-b:a", "160k", str(dst)]
            results = [Result(dst, "audio", title=stem)]
        elif op == "aud_voice":
            dst = out_dir / f"{stem}.ogg"
            args = [*base, "-i", str(src), "-vn", "-c:a", "libopus", "-b:a", "32k", "-application", "voip", str(dst)]
            results = [Result(dst, "voice")]
        elif op == "aud_small":
            dst = out_dir / f"{stem}_small.mp3"
            args = [*base, "-i", str(src), "-vn", "-c:a", "libmp3lame", "-b:a", "64k", "-ac", "1", str(dst)]
            results = [Result(dst, "audio", title=stem)]
        elif op == "vid_frame":
            dst = out_dir / f"{stem}.jpg"
            args = [*base, "-ss", "1", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(dst)]
            results = [Result(dst)]
        elif op == "vid_gif":
            dst = out_dir / f"{stem}.gif"
            flt = (
                "fps=10,scale='min(480,iw)':-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer"
            )
            args = [*base, "-t", str(GIF_SECONDS), "-i", str(src), "-vf", flt, "-loop", "0", str(dst)]
            results = [Result(dst, "animation", caption=f"Первые {GIF_SECONDS} секунд")]
        elif op == "vid_small":
            return await self._video_small(src, stem, out_dir)
        else:
            raise ConvertError("такой операции нет")
        code, err = await _exec(*args, timeout=600)
        if code:
            raise _fail("Файл", err)
        for r in results:
            _check_size(r.path)
        return results

    async def _video_small(self, src: Path, stem: str, out_dir: Path) -> list[Result]:
        dst = out_dir / f"{stem}_small.mp4"
        last_err = ""
        for crf, height in ((28, 720), (34, 480)):
            args = [
                *_ffmpeg_base(), "-i", str(src), "-vf", f"scale=-2:'min({height},ih)'", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", str(crf), "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(dst),
            ]  # fmt: skip
            code, last_err = await _exec(*args, timeout=900)
            if code:
                raise _fail("Видео", last_err)
            if dst.exists() and dst.stat().st_size <= MAX_OUTPUT:
                before, after = src.stat().st_size // 1024, dst.stat().st_size // 1024
                return [Result(dst, "video", caption=f"Было {before} КБ → стало {after} КБ")]
        raise ConvertError("даже после сжатия больше 50 МБ — Telegram не примет. Попробуйте обрезать видео")

    # ------------------------------------------------------------------ склейка в PDF
    async def merge(self, sources: Sequence[Path], out_dir: Path) -> Result:
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / "scan.pdf"
        await asyncio.to_thread(merge_pdf_sync, list(sources), dst)
        return Result(_check_size(dst), caption=f"Файлов: {len(sources)}")

    # ------------------------------------------------------------------ скачивание по ссылке
    async def download(self, url: str, mode: str, out_dir: Path) -> Result:
        if not self.caps.ytdlp:
            raise ConvertError("yt-dlp не установлен")
        out_dir.mkdir(parents=True, exist_ok=True)
        return await asyncio.to_thread(_download_sync, url, mode, out_dir, self.caps.ffmpeg)


def _download_sync(url: str, mode: str, out_dir: Path, have_ffmpeg: bool) -> Result:
    import yt_dlp

    audio = mode == "audio"
    if audio and not have_ffmpeg:
        raise ConvertError("для MP3 нужен ffmpeg")
    limit = MAX_OUTPUT - 1_000_000

    def too_long(info: dict[str, Any], *, incomplete: bool = False) -> str | None:
        duration = info.get("duration") or 0
        if duration and duration > (3600 if audio else 1800):
            return "слишком длинное (дольше часа для звука или 30 минут для видео)"
        if info.get("is_live"):
            return "это прямой эфир"
        return None

    opts: dict[str, Any] = {
        "outtmpl": str(out_dir / "%(title).80B.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "retries": 2,
        "max_filesize": limit,
        "restrictfilenames": False,
        "match_filter": too_long,
        "format": "bestaudio/best" if audio else f"b[height<=720][filesize<{limit}]/b[height<=720]/b[filesize<{limit}]/b",
    }
    if audio:
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    elif have_ffmpeg:
        opts["format"] = "bv*[height<=720]+ba/b[height<=720]/b"
        opts["merge_output_format"] = "mp4"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        text = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))
        log.info("download failed: %s", text)
        if "larger than max-filesize" in text or "max-filesize" in text:
            raise ConvertError("файл получается больше 50 МБ — Telegram такой не примет") from exc
        raise ConvertError("не получилось скачать: сайт не поддерживается или ссылка закрыта") from exc
    except Exception as exc:
        log.warning("download crashed: %s", exc)
        raise ConvertError("не получилось скачать") from exc
    if info is None:
        raise ConvertError("по ссылке ничего не нашлось или оно не подходит (слишком длинное, прямой эфир)")
    files = [
        p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() in {".mp3", ".mp4", ".m4a", ".webm", ".mkv", ".opus", ".ogg"}
    ]
    if not files:
        raise ConvertError("по ссылке ничего не нашлось или оно не подходит (слишком длинное, прямой эфир)")
    path = max(files, key=lambda p: p.stat().st_mtime)
    title = str(info.get("title") or path.stem)[:120]
    kind = "audio" if path.suffix.lower() in {".mp3", ".m4a", ".opus", ".ogg"} else "video"
    return Result(_check_size(path), kind, title=title, caption=title)
