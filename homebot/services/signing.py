"""🔏 Подпись релизов: Ed25519 над sha256 архива.

Открытые ключи автора лежат в `homebot/release_keys.txt` (base64, по одному в строке, `#` — комментарий).
Подпись — файл `homebot-X.Y.Z.tar.gz.sig` рядом с архивом: base64 подписи строки `uni-release:<sha256>`.
Создаёт ключ и подписывает архив `tools/sign_release.py`. Пока ключей нет, архивы просто «без подписи».
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path

KEYS_FILE = Path(__file__).resolve().parents[1] / "release_keys.txt"
PREFIX = b"uni-release:"

SIGNED, UNSIGNED, BAD, NO_KEYS = "signed", "unsigned", "bad", "nokeys"


def message(sha256_hex: str) -> bytes:
    return PREFIX + sha256_hex.strip().lower().encode()


def load_keys(path: Path = KEYS_FILE) -> list[bytes]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    keys = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            raw = base64.b64decode(line, validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(raw) == 32:
            keys.append(raw)
    return keys


def verify(sha256_hex: str, signature: str, keys: list[bytes]) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        sig = base64.b64decode(signature.strip(), validate=True)
    except (binascii.Error, ValueError):
        return False
    for raw in keys:
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(sig, message(sha256_hex))
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def sig_path(archive: Path) -> Path:
    return archive.with_name(archive.name + ".sig")


def status(archive: Path, sha256_hex: str, keys: list[bytes] | None = None) -> str:
    keys = load_keys() if keys is None else keys
    sig = sig_path(archive)
    if not sig.is_file():
        return UNSIGNED if keys else NO_KEYS
    if not keys:
        return NO_KEYS
    try:
        text = sig.read_text(encoding="utf-8")[:512]
    except (OSError, UnicodeDecodeError):
        return BAD
    return SIGNED if verify(sha256_hex, text, keys) else BAD


LABELS = {
    SIGNED: "🔏 Подпись автора проверена",
    UNSIGNED: "⚠️ Без подписи: пришлите файл .sig, если он есть рядом с архивом",
    BAD: "⛔ Подпись не сходится: архив изменён или подписан чужим ключом",
    NO_KEYS: "Подпись не проверяется: ключи автора ещё не заданы",
}
