"""Load local provider credentials encrypted for the current Windows user."""
from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

SECRET_PATH = Path(__file__).resolve().parent.parent / ".local" / "agent-secrets.dpapi"
FIELDS = {"FIRECRAWL_API_KEY", "DEEPSEEK_API_KEY", "CRK_SEARCH_PROVIDER", "CRK_LLM_PROVIDER", "DEEPSEEK_MODEL"}


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Local encrypted credentials require Windows; use environment variables on other systems")
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = _Blob(len(data), buffer), _Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = library.CryptUnprotectData if decrypt else library.CryptProtectData
    fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    fn.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError("Unable to access local provider credentials for this Windows account")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def read_secrets() -> dict[str, str]:
    if not SECRET_PATH.exists():
        return {}
    # A DPAPI file can belong to a different Windows account (for example
    # after a machine restore or when the service is started by a bundled
    # runtime).  Environment variables are still valid configuration, so a
    # credential-store read failure must leave the service in an explicit
    # "provider not configured" state instead of preventing HTTP startup.
    try:
        values = json.loads(_crypt(SECRET_PATH.read_bytes(), decrypt=True))
    except (OSError, RuntimeError, ValueError, TypeError):
        return {}
    return {key: value for key, value in values.items() if key in FIELDS and isinstance(value, str)}


def save_secrets(values: dict[str, str]) -> None:
    merged = read_secrets()
    merged.update({key: value for key, value in values.items() if key in FIELDS and isinstance(value, str)})
    encrypted = _crypt(json.dumps(merged).encode("utf-8"))
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SECRET_PATH.write_bytes(encrypted)


def load_provider_environment() -> None:
    for key, value in read_secrets().items():
        os.environ.setdefault(key, value)
