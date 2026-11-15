"""Theme the Windows frame while retaining the native dialog controls."""
import ctypes
import sys

from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog


def _colorref(color):
    color = QColor(color)
    return color.red() | (color.green() << 8) | (color.blue() << 16)


class SettingsWindow(QDialog):
    _caption = None

    def set_caption_colors(self, background, foreground, dark):
        self._caption = background, foreground, dark
        self._apply_caption()

    def event(self, event):
        handled = super().event(event)
        if event.type() in (QEvent.Type.WinIdChange, QEvent.Type.Show):
            self._apply_caption()
        return handled

    def _apply_caption(self):
        if sys.platform != "win32" or self._caption is None:
            return
        handle = self.effectiveWinId()
        if not handle:
            return
        hwnd = ctypes.c_void_p(int(handle))
        try:
            set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        except (AttributeError, OSError):
            return
        background, foreground, dark = self._caption
        enabled = ctypes.c_int(dark)
        result = set_attribute(hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
        build = sys.getwindowsversion().build
        if result != 0 and 17763 <= build < 18985:
            # Early Windows 10 builds used attribute 19.
            set_attribute(hwnd, 19, ctypes.byref(enabled), ctypes.sizeof(enabled))
        if build >= 22000:
            for attribute, color in ((35, background), (36, foreground)):
                value = ctypes.c_uint(_colorref(color))
                set_attribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
