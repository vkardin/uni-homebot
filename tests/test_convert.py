"""🧰 Конвертер: определение видов файлов, набор операций по возможностям сервера, реальные преобразования (если есть программы)."""

import asyncio
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from homebot.services import convert
from homebot.services.convert import Caps, Converter, ConvertError, describe_caps, detect, kind_of, ops_for
from homebot.services.urlguard import public_http_url

FULL = Caps(*([True] * 9))


def keys(kind: str, caps: Caps, name: str = "") -> list[str]:
    return [o.key for o in ops_for(kind, caps, name)]


def test_kind_of():
    assert kind_of("IMG_1.HEIC") == "image" and kind_of("a.jpeg") == "image"
    assert kind_of("report.PDF") == "pdf" and kind_of("x", "application/pdf") == "pdf"
    assert kind_of("movie.mkv") == "video" and kind_of("song.flac") == "audio"
    assert kind_of("cv.docx") == "doc" and kind_of("table.xlsx") == "doc"
    assert kind_of("data.json") is None and kind_of("archive.tar.gz") is None
    assert kind_of("noext", "video/mp4") == "video" and kind_of("noext", "text/plain") is None


def test_ops_depend_on_installed_programs():
    assert keys("image", Caps()) == [] and keys("video", Caps()) == [] and keys("doc", Caps()) == []
    assert "vid_small" in keys("video", Caps(ffmpeg=True)) and "img_jpg" not in keys("video", Caps(ffmpeg=True))
    assert "img_jpg" in keys("image", Caps(pillow=True)) and "img_ocr" not in keys("image", Caps(pillow=True))
    assert "img_ocr" in keys("image", Caps(pillow=True, tesseract=True))
    # HEIC без pillow-heif не предлагаем, обычные картинки — да
    assert keys("image", Caps(pillow=True), "x.heic") == [] and keys("image", Caps(pillow=True, heif=True), "x.heic")
    assert keys("doc", Caps(office=True)) == ["doc_pdf"]
    assert keys("pdf", Caps(pypdf=True)) == ["pdf_txt"]
    assert keys("pdf", FULL) == ["pdf_txt", "pdf_png", "pdf_small"]
    assert len({k for kind in convert.KINDS for k in keys(kind, FULL)}) == sum(len(keys(kind, FULL)) for kind in convert.KINDS)


def test_detect_and_describe():
    caps = detect()
    assert isinstance(caps, Caps) and len(describe_caps(caps)) == 9
    assert all(isinstance(ok, bool) and name and what for ok, name, what in describe_caps(caps))


def test_stem_is_safe():
    assert convert._stem("../../etc/passwd") == "passwd"
    assert convert._stem("мой файл (1).docx") == "мой файл _1"
    assert convert._stem("....") == "file" and len(convert._stem("a" * 300)) <= 60


def test_exec_timeout_and_missing_program():
    async def go():
        try:
            await convert._exec("sleep", "5", timeout=0.2)
        except ConvertError as exc:
            assert "долго" in str(exc)
        else:
            raise AssertionError("no timeout")
        try:
            await convert._exec("definitely-not-a-program", timeout=1)
        except ConvertError as exc:
            assert "нет программы" in str(exc)
        else:
            raise AssertionError("no error")

    if shutil.which("sleep"):
        asyncio.run(go())


def test_public_url_guard():
    def bad(url: str) -> str:
        try:
            asyncio.run(public_http_url(url))
        except ValueError as exc:
            return str(exc)
        raise AssertionError(f"{url} was accepted")

    for url in (
        "ftp://example.com/a",
        "http://localhost/x",
        "http://intranet/x",
        "http://127.0.0.1:8088/",
        "http://192.168.0.165:11434/",
        "http://100.100.1.1/",
        "http://[::1]/",
        "https://user:pw@example.com/",
        "",
        "https://" + "a" * 400 + ".com",
    ):
        assert bad(url)


# ----------------------------------------------------------------------- реальные преобразования
def _image(path: Path, mode: str = "RGBA", size: tuple[int, int] = (800, 600)) -> Path:
    from PIL import Image

    im = Image.new(mode, size, (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30))
    im.save(path)
    return path


def test_image_operations():
    pytest.importorskip("PIL")
    from PIL import Image

    conv = Converter(Path(tempfile.gettempdir()))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = _image(root / "in.png")
        for op, fmt in (("img_jpg", "JPEG"), ("img_png", "PNG"), ("img_webp", "WEBP"), ("img_rot", "PNG")):
            (res,) = asyncio.run(conv.run(op, src, "Фото 1.png", root / op))
            assert Image.open(res.path).format == fmt and res.kind == "document"
        (rot,) = asyncio.run(conv.run("img_rot", src, "a.png", root / "r"))
        assert Image.open(rot.path).size == (600, 800)
        (pdf,) = asyncio.run(conv.run("img_pdf", src, "a.png", root / "pdf"))
        assert pdf.path.read_bytes().startswith(b"%PDF")
        big = _image(root / "big.png", "RGB", (4000, 3000))
        (small,) = asyncio.run(conv.run("img_small", big, "big.png", root / "small"))
        assert max(Image.open(small.path).size) <= 2048
        broken = root / "broken.png"
        broken.write_bytes(b"not an image" * 10)
        try:
            asyncio.run(conv.run("img_jpg", broken, "broken.png", root / "bad"))
        except ConvertError as exc:
            assert "не получилось" in str(exc)
        else:
            raise AssertionError("broken image accepted")


def test_merge_images_and_pdf_into_one_pdf():
    pytest.importorskip("PIL")
    pypdf = pytest.importorskip("pypdf")
    conv = Converter(Path(tempfile.gettempdir()))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a = _image(root / "a.png", "RGB")
        b = _image(root / "b.jpg", "RGB", (300, 300))
        two = root / "two.pdf"
        a_img = convert._flat(convert._load(a))
        a_img.save(two, "PDF", save_all=True, append_images=[a_img])
        res = asyncio.run(conv.merge([a, two, b], root / "out"))
        assert len(pypdf.PdfReader(str(res.path)).pages) == 4 and "3" in res.caption


def test_pdf_text_without_text_layer_is_explained():
    pytest.importorskip("PIL")
    pytest.importorskip("pypdf")
    conv = replace_caps(Converter(Path(tempfile.gettempdir())), Caps(pypdf=True))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pdf = root / "scan.pdf"
        _image(root / "i.png", "RGB").rename(root / "i2.png")
        convert._flat(convert._load(root / "i2.png")).save(pdf, "PDF")
        try:
            asyncio.run(conv.run("pdf_txt", pdf, "scan.pdf", root / "out"))
        except ConvertError as exc:
            assert "нет текста" in str(exc)
        else:
            raise AssertionError("expected an explanation")


def replace_caps(conv: Converter, caps: Caps) -> Converter:
    conv.caps = replace(caps)
    return conv


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *args], check=True, timeout=60)


def test_audio_and_video_operations():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg is not installed")
    conv = Converter(Path(tempfile.gettempdir()))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wav, mp4 = root / "tone.wav", root / "clip.mp4"
        _ff("-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(wav))
        _ff("-f", "lavfi", "-i", "testsrc=size=640x360:rate=15:duration=2", "-f", "lavfi", "-i", "sine=frequency=330:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(mp4))  # fmt: skip

        def probe(path: Path, entry: str) -> str:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", f"stream={entry}", "-of", "csv=p=0", str(path)],
                                 capture_output=True, text=True, check=True, timeout=30)  # fmt: skip
            return out.stdout.strip()

        cases = (("aud_mp3", wav, "mp3", "audio"), ("aud_m4a", wav, "aac", "audio"), ("aud_voice", wav, "opus", "voice"),
                 ("aud_small", wav, "mp3", "audio"), ("vid_mp3", mp4, "mp3", "audio"))  # fmt: skip
        for op, src, codec, kind in cases:
            (res,) = asyncio.run(conv.run(op, src, src.name, root / op))
            assert res.kind == kind and probe(res.path, "codec_name").splitlines()[0] == codec, op
        (small,) = asyncio.run(conv.run("vid_small", mp4, "clip.mp4", root / "vs"))
        assert small.kind == "video" and "h264" in probe(small.path, "codec_name")
        (gif,) = asyncio.run(conv.run("vid_gif", mp4, "clip.mp4", root / "gif"))
        assert gif.kind == "animation" and gif.path.read_bytes()[:3] == b"GIF"
        (frame,) = asyncio.run(conv.run("vid_frame", mp4, "clip.mp4", root / "fr"))
        assert frame.path.read_bytes()[:2] == b"\xff\xd8"
        broken = root / "bad.mp4"
        broken.write_bytes(b"\x00" * 2000)
        try:
            asyncio.run(conv.run("vid_small", broken, "bad.mp4", root / "x"))
        except ConvertError as exc:
            assert "не получилось" in str(exc)
        else:
            raise AssertionError("broken video accepted")


def test_oversized_result_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        big = Path(tmp) / "big.bin"
        with big.open("wb") as fh:
            fh.truncate(convert.MAX_OUTPUT + 1)
        try:
            convert._check_size(big)
        except ConvertError as exc:
            assert "50 МБ" in str(exc)
        else:
            raise AssertionError("oversized file accepted")
        empty = Path(tmp) / "empty"
        empty.touch()
        try:
            convert._check_size(empty)
        except ConvertError:
            pass
        else:
            raise AssertionError("empty file accepted")


# ----------------------------------------------------------------------- обработчики
def test_job_store_and_guest_quota():
    pytest.importorskip("aiogram.fsm.context")
    from types import SimpleNamespace

    from homebot.handlers import tools

    job = tools.Job(7, "fid", "a.png", "image", 100, False, 9_999_999_999)
    ref = tools._put(job)
    assert tools._get(ref, 7) is job and tools._get(ref, 8) is None and tools._get(ref + 1, 7) is None
    expired = tools.Job(7, "f", "a", "image", 1, False, 1)
    assert tools._get(tools._put(expired), 7) is None
    guest = SimpleNamespace(id=42)
    tools._recent.clear()
    assert not any(tools.over_quota(42, guest) for _ in range(tools.GUEST_PER_HOUR))
    assert tools.over_quota(42, guest) and not tools.over_quota(1, None)
    assert asyncio.run(tools.tools_ok(object(), None))
    assert not asyncio.run(tools.tools_ok(object(), SimpleNamespace(can=lambda f: False)))
    assert asyncio.run(tools.tools_ok(object(), SimpleNamespace(can=lambda f: f == "tools")))
