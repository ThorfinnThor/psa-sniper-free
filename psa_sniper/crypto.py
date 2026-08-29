from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

FORMAT = "psa-sniper-aesgcm-v1"
ITERATIONS = 310_000


class EncryptionError(RuntimeError):
    pass


def _derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    if len(password) < 16:
        raise EncryptionError("DASHBOARD_PASSWORD muss mindestens 16 Zeichen lang sein")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_bytes(data: bytes, password: str) -> dict[str, Any]:
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = _derive_key(password, salt, ITERATIONS)
    ciphertext = AESGCM(key).encrypt(iv, data, FORMAT.encode("utf-8"))
    return {
        "format": FORMAT,
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "iv": base64.b64encode(iv).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def decrypt_bytes(envelope: dict[str, Any], password: str) -> bytes:
    try:
        if envelope.get("format") != FORMAT:
            raise EncryptionError("Unbekanntes Verschlüsselungsformat")
        iterations = int(envelope["iterations"])
        salt = base64.b64decode(envelope["salt"])
        iv = base64.b64decode(envelope["iv"])
        ciphertext = base64.b64decode(envelope["ciphertext"])
        key = _derive_key(password, salt, iterations)
        return AESGCM(key).decrypt(iv, ciphertext, FORMAT.encode("utf-8"))
    except EncryptionError:
        raise
    except Exception as exc:
        raise EncryptionError(
            "Entschlüsselung fehlgeschlagen. Passwort falsch oder State beschädigt."
        ) from exc


def encrypt_json(data: Any, password: str) -> dict[str, Any]:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return encrypt_bytes(raw, password)


def decrypt_json(envelope: dict[str, Any], password: str) -> Any:
    return json.loads(decrypt_bytes(envelope, password).decode("utf-8"))


def encrypt_file(input_path: Path, output_path: Path, password: str) -> None:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(encrypt_json(data, password), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def decrypt_file(input_path: Path, output_path: Path, password: str) -> None:
    envelope = json.loads(input_path.read_text(encoding="utf-8"))
    data = decrypt_json(envelope, password)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
