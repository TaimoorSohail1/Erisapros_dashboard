"""Keep frozen Python DLLs out of Windows system helpers, without changing callers' PATH."""

import ctypes
import os
import sys
import threading
from contextlib import contextmanager

_loader_lock = threading.RLock()


def _get_dll_directory() -> str | None:
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
    kernel.GetDllDirectoryW.restype = ctypes.c_uint32
    length = kernel.GetDllDirectoryW(0, None)
    if not length:
        return None
    buffer = ctypes.create_unicode_buffer(length + 1)
    if not kernel.GetDllDirectoryW(len(buffer), buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.value


def _set_dll_directory(directory: str | None) -> None:
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    kernel.SetDllDirectoryW.restype = ctypes.c_int
    if not kernel.SetDllDirectoryW(directory):
        raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def windows_system_environment(environment: dict[str, str]):
    child = dict(environment)
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        yield child
        return
    bundle = os.path.normcase(os.path.abspath(sys._MEIPASS))
    def bundled(path: str) -> bool:
        absolute = os.path.normcase(os.path.abspath(path.strip('"')))
        return absolute == bundle or absolute.startswith(bundle + os.sep)
    child['PATH'] = os.pathsep.join(path for path in child.get('PATH', '').split(os.pathsep)
                                    if path and not bundled(path))
    # Maintenance commands do not run a browser in this process. Restore the
    # bundled search path even when the helper fails; never sanitize Chromium's env.
    with _loader_lock:
        previous = _get_dll_directory()
        _set_dll_directory(None)
        try:
            yield child
        finally:
            _set_dll_directory(previous)
