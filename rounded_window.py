"""Rounded, translucent Qt windows without per-pixel UpdateLayeredWindow.

Normal window painting plus uniform opacity is compatible with display
affinity on Windows 10. A native region removes the corners entirely.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QObject, QRectF
from PySide6.QtGui import QColor, QPainterPath, QPalette, QRegion


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
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.window.rect()), self.radius, self.radius)
        self.window.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.update_shape()
        return False


def configure_rounded_window(window, **options):
    # Keep the filter alive for exactly as long as its window.
    window._rounded_window = RoundedWindow(window, **options)
    return window._rounded_window
