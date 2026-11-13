"""Interactive screen-region selection; pixels remain in memory."""
from __future__ import annotations

from PySide6.QtCore import QBuffer, QIODevice, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


# Inline images are base64 encoded in JSON; leave room below the API's 20 MB request limit.
MAX_INLINE_BYTES = 12 * 1024 * 1024


def drag_rect(start: QPoint, end: QPoint) -> QRect:
    return QRect(min(start.x(), end.x()), min(start.y(), end.y()),
                 abs(end.x() - start.x()), abs(end.y() - start.y()))


def map_selection_to_pixels(selection: QRect, logical: QRect, pixmap: QPixmap) -> QRect:
    """Map a widget's logical coordinates to the grabbed screen's real pixels."""
    if logical.width() <= 0 or logical.height() <= 0 or pixmap.isNull():
        return QRect()
    area = selection.normalized().intersected(logical)
    x0 = round((area.left() - logical.left()) * pixmap.width() / logical.width())
    y0 = round((area.top() - logical.top()) * pixmap.height() / logical.height())
    x1 = round((area.left() + area.width() - logical.left()) * pixmap.width() / logical.width())
    y1 = round((area.top() + area.height() - logical.top()) * pixmap.height() / logical.height())
    return QRect(x0, y0, max(0, x1 - x0), max(0, y1 - y0))


def encode_image(image: QImage) -> tuple[bytes, str]:
    """Keep fine text lossless where possible, with a bounded inline payload."""
    if image.isNull():
        raise ValueError("Порожня виділена область.")

    def encode(source: QImage, fmt: str, quality: int = -1) -> bytes:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not source.save(buffer, fmt, quality):
            raise ValueError("Не вдалося підготувати зображення.")
        return bytes(buffer.data())

    png = encode(image, "PNG")
    if len(png) <= MAX_INLINE_BYTES:
        return png, "image/png"
    jpeg = encode(image, "JPEG", 92)
    if len(jpeg) <= MAX_INLINE_BYTES:
        return jpeg, "image/jpeg"
    factor = min(1.0, 3200 / max(image.width(), image.height()))
    smaller = image.scaled(round(image.width() * factor), round(image.height() * factor),
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    jpeg = encode(smaller, "JPEG", 88)
    if len(jpeg) > MAX_INLINE_BYTES:
        raise ValueError("Виділена область завелика для одного запиту.")
    return jpeg, "image/jpeg"


class ScreenSelection(QWidget):
    selected = Signal(QImage)
    cancelled = Signal()

    def __init__(self, screen, screenshot: QPixmap):
        super().__init__(None, Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self._background = screenshot
        self._origin: QPoint | None = None
        self._selection = QRect()
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self._background)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 125))
        if not self._selection.isNull():
            area = self._selection.normalized().intersected(self.rect())
            source = map_selection_to_pixels(area, self.rect(), self._background)
            painter.drawPixmap(area, self._background, source)
            painter.setPen(QPen(QColor(255, 255, 255), 2))
            painter.drawRect(area.adjusted(0, 0, -1, -1))
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        painter.drawText(QRect(16, 12, self.width() - 32, 36),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "Виділіть область мишею · Esc або права кнопка — скасувати")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
        elif event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._selection = QRect()
            self.update()

    def mouseMoveEvent(self, event):
        if self._origin is not None:
            self._selection = drag_rect(self._origin, event.position().toPoint())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        area = drag_rect(self._origin, event.position().toPoint()).intersected(self.rect())
        self._origin = None
        if area.width() < 12 or area.height() < 12:
            self._selection = QRect()
            self.update()
            return
        pixels = map_selection_to_pixels(area, self.rect(), self._background)
        image = self._background.copy(pixels).toImage()
        if not image.isNull():
            self.selected.emit(image)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)
