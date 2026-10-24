from __future__ import annotations

import base64
import ctypes
import json
import os
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Any


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect(data: bytes) -> bytes:
    if not hasattr(ctypes, "windll"):
        return data
    source, _keep = _blob(data)
    result = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "GZHReader", None, None, None, 0, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def unprotect(data: bytes) -> bytes:
    if not hasattr(ctypes, "windll"):
        return data
    source, _keep = _blob(data)
    result = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


class CredentialVault:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        encoded = base64.b64encode(protect(raw))
        fd, temporary = tempfile.mkstemp(prefix=f".{name}-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.root / f"{name}.bin")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, name: str) -> dict[str, Any]:
        path = self.root / f"{name}.bin"
        if not path.exists():
            return {}
        try:
            payload = json.loads(unprotect(base64.b64decode(path.read_bytes(), validate=True)).decode("utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def clear(self, name: str) -> None:
        path = self.root / f"{name}.bin"
        if path.exists():
            path.unlink()
