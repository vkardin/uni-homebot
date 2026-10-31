"""Подпись релизов UNI.

    python tools/sign_release.py keygen                # создать ключ, напечатать открытый
    python tools/sign_release.py sign homebot-X.Y.Z.tar.gz  # создать homebot-X.Y.Z.tar.gz.sig

Секретный ключ хранится в ~/.config/uni/release.key (права 600) и никуда не отправляется.
Открытый ключ добавьте строкой в homebot/release_keys.txt.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys
from pathlib import Path

KEY = Path.home() / ".config" / "uni" / "release.key"


def main(argv: list[str]) -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = serialization.Encoding.Raw
    if argv[:1] == ["keygen"]:
        if KEY.exists():
            print(f"Ключ уже есть: {KEY}", file=sys.stderr)
            return 1
        key = Ed25519PrivateKey.generate()
        KEY.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key.private_bytes(raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))
        print(base64.b64encode(key.public_key().public_bytes(raw, serialization.PublicFormat.Raw)).decode())
        return 0
    if len(argv) == 2 and argv[0] == "sign":
        key = Ed25519PrivateKey.from_private_bytes(KEY.read_bytes())
        archive = Path(argv[1])
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        sig = base64.b64encode(key.sign(b"uni-release:" + digest.encode())).decode()
        archive.with_name(archive.name + ".sig").write_text(sig + "\n", encoding="utf-8")
        print(f"{archive.name}.sig")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
