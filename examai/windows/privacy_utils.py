"""Verified capture exclusion for this process's own Windows windows."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
import sys


WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011
GA_ROOT = 2


@dataclass(frozen=True)
class AffinityResult:
    success: bool
    affinity: int | None = None
    message: str = ""
    error_code: int = 0

    @property
    def excluded(self) -> bool:
        return self.success and self.affinity == WDA_EXCLUDEFROMCAPTURE


class WindowsCaptureAPI:
    """Use pointer-sized HWNDs and keep GetLastError next to each failed call."""

    def __init__(self):
        self.available = sys.platform == "win32"
        self.user32 = self.dwmapi = None
        if not self.available:
            return
        try:
            self.user32 = ctypes.WinDLL("user32", use_last_error=True)
            self.dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
            signatures = (
                (self.user32.IsWindow, [wintypes.HWND], wintypes.BOOL),
                (self.user32.GetAncestor, [wintypes.HWND, wintypes.UINT], wintypes.HWND),
                (self.user32.GetWindowThreadProcessId,
                 [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
                (self.user32.SetWindowDisplayAffinity,
                 [wintypes.HWND, wintypes.DWORD], wintypes.BOOL),
                (self.user32.GetWindowDisplayAffinity,
                 [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
                (self.dwmapi.DwmIsCompositionEnabled,
                 [ctypes.POINTER(wintypes.BOOL)], ctypes.c_long),
                (self.dwmapi.DwmFlush, [], ctypes.c_long),
            )
            for function, argtypes, restype in signatures:
                function.argtypes = argtypes
                function.restype = restype
        except (AttributeError, OSError):
            self.available = False

    @staticmethod
    def _failure(operation: str) -> AffinityResult:
        code = ctypes.get_last_error()
        return AffinityResult(False, message=f"{operation}: Windows error {code}", error_code=code)

    def read(self, hwnd: int) -> AffinityResult:
        if not self.available:
            return AffinityResult(False, message="Ghost Mode requires Windows 10 (2004+) or Windows 11.")
        affinity = wintypes.DWORD()
        ctypes.set_last_error(0)
        if not self.user32.GetWindowDisplayAffinity(wintypes.HWND(hwnd), ctypes.byref(affinity)):
            return self._failure("GetWindowDisplayAffinity")
        return AffinityResult(True, affinity=int(affinity.value))

    def apply(self, hwnd: int, enabled: bool) -> AffinityResult:
        if not self.available:
            return AffinityResult(False, message="Ghost Mode requires Windows 10 (2004+) or Windows 11.")
        if not hwnd or not self.user32.IsWindow(wintypes.HWND(hwnd)):
            return AffinityResult(False, message="The native window has not been created.")
        if self.user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT) != hwnd:
            return AffinityResult(False, message="Capture exclusion requires a top-level window.")
        process = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(process))
        if process.value != os.getpid():
            return AffinityResult(False, message="The window does not belong to this application.")
        if enabled:
            version = sys.getwindowsversion()
            if version.major < 10 or version.build < 19041:
                return AffinityResult(False, message="Ghost Mode requires Windows 10 version 2004 (build 19041) or newer.")
            composition = wintypes.BOOL()
            hresult = self.dwmapi.DwmIsCompositionEnabled(ctypes.byref(composition))
            if hresult != 0 or not composition.value:
                return AffinityResult(False, message="Desktop Window Manager composition is unavailable.")
        expected = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
        ctypes.set_last_error(0)
        if not self.user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), expected):
            return self._failure("SetWindowDisplayAffinity")
        # A successful setter precedes the compositor's next frame. Wait for
        # the DWM batch before considering this window's flag settled.
        self.dwmapi.DwmFlush()
        observed = self.read(hwnd)
        if not observed.success:
            return observed
        if observed.affinity != expected:
            return AffinityResult(
                False, affinity=observed.affinity,
                message=f"Capture setting was not confirmed (expected 0x{expected:02x}, got 0x{observed.affinity:02x}).",
            )
        return observed


_api: WindowsCaptureAPI | None = None


def _default_api() -> WindowsCaptureAPI:
    global _api
    if _api is None:
        _api = WindowsCaptureAPI()
    return _api


def set_capture_exclusion(hwnd: int, enabled: bool) -> AffinityResult:
    return _default_api().apply(int(hwnd), bool(enabled))


def get_capture_exclusion(hwnd: int) -> AffinityResult:
    return _default_api().read(int(hwnd))


def set_window_stealth_mode(hwnd: int, enabled: bool) -> bool:
    """Compatibility with older main.py; success always includes read-back."""
    return set_capture_exclusion(hwnd, enabled).success
