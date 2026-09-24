"""Small Windows DPAPI-backed store for the local-agent device credential."""

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("The FT Williams local-agent credential store requires Windows DPAPI.")


def _input_blob(value: bytes) -> tuple[_DataBlob, object]:
    buffer = ctypes.create_string_buffer(value)
    return (
        _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))),
        buffer,
    )


def protect_bytes(value: bytes) -> bytes:
    _require_windows()
    source, source_buffer = _input_blob(value)
    protected = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "ERISAPros FT Williams local agent",
        None,
        None,
        None,
        0x1,
        ctypes.byref(protected),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(protected.pbData, ctypes.c_void_p))
        del source_buffer


def unprotect_bytes(value: bytes) -> bytes:
    _require_windows()
    source, source_buffer = _input_blob(value)
    clear = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0x1,
        ctypes.byref(clear),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(clear.pbData, clear.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(clear.pbData, ctypes.c_void_p))
        del source_buffer


def save_secret_json(path: str | Path, payload: dict) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    clear = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    protected = protect_bytes(clear)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(protected)
    temporary.replace(destination)
    return destination


def load_secret_json(path: str | Path) -> dict:
    source = Path(path).expanduser().resolve()
    try:
        clear = unprotect_bytes(source.read_bytes())
        payload = json.loads(clear.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("The local-agent credential file is missing or invalid.") from exc
    if not isinstance(payload, dict):
        raise ValueError("The local-agent credential file is invalid.")
    return payload
