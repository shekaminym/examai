"""Rounded, translucent Qt windows without per-pixel UpdateLayeredWindow.

Normal window painting plus uniform opacity is compatible with display
affinity on Windows 10. A native region removes the corners entirely.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QColor, QPalette, QRegion


class RoundedWindow(QObject):
    def __init__(self, window, *, radius=14, opacity=0.95, color="#0a0a0a"):
        super().__init__(window)
        self.window = window
        self.radius = radius
        try:
            opacity = float(opacity)
        except (TypeError, ValueError):
            opacity = 0.95
        if not math.isfinite(opacity):
            opacity = 0.95
        self.opacity = max(0.30, min(1.0, opacity))
        # Do not enable WA_TranslucentBackground here: that selects Qt's
        # per-pixel raster path, which conflicts with display affinity on Win10.
        palette = window.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(color))
        window.setPalette(palette)
        window.setAutoFillBackground(True)
        window.setWindowOpacity(self.opacity)
        window.installEventFilter(self)
        self.update_shape()

    def update_shape(self):
        width, height = self.window.width(), self.window.height()
        radius = min(max(0, int(self.radius)), width // 2, height // 2)
        if not radius:
            self.window.setMask(QRegion(0, 0, width, height))
            return
        # A polygonal path approximates a quarter-circle with a few long
        # edges. Rasterize the true circle row by row, including at high DPI.
        region = QRegion(0, radius, width, max(0, height - 2 * radius))
        for y in range(radius):
            distance = radius - y - 0.5
            inset = max(0, math.ceil(radius - math.sqrt(radius * radius - distance * distance) - 0.5))
            strip = QRegion(inset, y, width - 2 * inset, 1)
            region = region.united(strip)
            region = region.united(QRegion(inset, height - y - 1, width - 2 * inset, 1))
        self.window.setMask(region)

    def eventFilter(self, watched, event):
        if watched is getattr(self, "window", None) and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.update_shape()
        return False


def configure_rounded_window(window, **options):
    # Keep the filter alive for exactly as long as its window.
    window._rounded_window = RoundedWindow(window, **options)
    return window._rounded_window
