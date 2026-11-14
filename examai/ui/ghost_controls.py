"""Painted Ghost Mode switch and lock; no emoji/font icon dependencies."""
from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton


class GhostToggle(QAbstractButton):
    blocked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(154, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Ghost Mode")
        self.locked = False
        self.verified = False
        self.error = ""
        self._position = 0.0
        self._animation_enabled = True
        self.animation = QPropertyAnimation(self, b"thumbPosition", self)
        self.animation.setDuration(240)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.toggled.connect(self._animate)
        self.setText("Ghost Mode OFF")

    def _get_position(self):
        return self._position

    def _set_position(self, value):
        self._position = float(value)
        self.update()

    thumbPosition = Property(float, _get_position, _set_position)

    def _animate(self, checked):
        self.animation.stop()
        if self._animation_enabled:
            self.animation.setStartValue(self._position)
            self.animation.setEndValue(1.0 if checked else 0.0)
            self.animation.start()
        else:
            self._set_position(1.0 if checked else 0.0)
        self.setText("Ghost Mode ON" if checked else "Ghost Mode OFF")

    def set_animation_enabled(self, enabled):
        self._animation_enabled = bool(enabled)
        if not enabled:
            self.animation.stop()
            self._set_position(1.0 if self.isChecked() else 0.0)

    def set_locked(self, locked):
        self.locked = bool(locked)
        self.setAccessibleDescription("Locked" if locked else "Unlocked")
        self.update()

    def set_feedback(self, enabled, requested, error=""):
        self.verified = bool(enabled)
        self.error = error
        self.setChecked(bool(requested))
        self.setText("Ghost Mode ERROR" if error else (
            "Ghost Mode ON" if enabled else "Ghost Mode OFF"
        ))
        self.update()

    def nextCheckState(self):
        if self.locked:
            self.blocked.emit()
            return
        super().nextCheckState()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pill = QRectF(0.75, 0.75, self.width() - 1.5, self.height() - 1.5)
        fill = QLinearGradient(0, 0, self.width(), 0)
        fill.setColorAt(0, QColor("#294b92" if self.isChecked() else "#263e71"))
        fill.setColorAt(1, QColor("#644fb0" if self.isChecked() else "#273b68"))
        painter.setBrush(fill)
        painter.setPen(QPen(QColor("#a87552" if self.error else (
            "#aaa8f3" if self.verified else "#6b84b6"
        )), 1.0))
        painter.drawRoundedRect(pill, 15, 15)
        clip = QPainterPath()
        clip.addRoundedRect(pill, 15, 15)
        painter.setClipPath(clip)
        font = QFont("Segoe UI")
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        progress = self._position
        # Two captions cross-fade and slide beneath the opaque moving thumb.
        painter.setPen(QColor("#d2dffc"))
        painter.setOpacity(1.0 - progress)
        painter.drawText(QRectF(34, 0, 112, 30), Qt.AlignmentFlag.AlignCenter, "Ghost Mode OFF")
        painter.setOpacity(progress)
        painter.setPen(QColor("#f5f1ff" if not self.error else "#edbe92"))
        painter.drawText(QRectF(7, 0, 112, 30), Qt.AlignmentFlag.AlignCenter,
                         "Ghost Mode !" if self.error else "Ghost Mode ON")
        painter.setOpacity(1.0)
        center = QPointF(15 + progress * (self.width() - 30), 15)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#6e95e9" if not self.locked else "#a88ded"))
        painter.drawEllipse(center, 11, 11)
        # A small, recognisable ghost drawn directly as a vector path.
        painter.save()
        painter.translate(center.x() - 6, center.y() - 7)
        ghost = QPainterPath()
        ghost.moveTo(0, 13)
        ghost.lineTo(0, 6)
        ghost.cubicTo(0, -2, 12, -2, 12, 6)
        ghost.lineTo(12, 13)
        ghost.lineTo(9, 11)
        ghost.lineTo(6, 13)
        ghost.lineTo(3, 11)
        ghost.closeSubpath()
        painter.setBrush(QColor("#f5f8ff"))
        painter.drawPath(ghost)
        painter.setBrush(QColor("#5072b3"))
        painter.drawEllipse(QPointF(4, 6), 1, 1.4)
        painter.drawEllipse(QPointF(8, 6), 1, 1.4)
        painter.restore()
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#b5b5b5"), 1, Qt.PenStyle.DotLine))
            painter.drawRoundedRect(pill.adjusted(2, 2, -2, -2), 12, 12)


class GhostLockButton(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(26, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Lock Ghost Mode control")
        self.toggled.connect(lambda _checked: self.update())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#8d82d3" if self.isChecked() else "#5f79b4"), 1))
        painter.setBrush(QColor("#3e4a87" if self.isChecked() else "#293e72"))
        painter.drawRoundedRect(QRectF(0.75, 0.75, 24.5, 28.5), 9, 9)
        color = QColor("#e4d7ff" if self.isChecked() else "#c4d9ff")
        painter.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        if self.isChecked():
            path.moveTo(9, 14)
            path.lineTo(9, 10)
            path.cubicTo(9, 4, 17, 4, 17, 10)
            path.lineTo(17, 14)
        else:
            path.moveTo(11, 14)
            path.lineTo(11, 10)
            path.cubicTo(11, 4, 19, 4, 19, 10)
        painter.drawPath(path)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(6, 13, 14, 11), 3, 3)
        painter.setBrush(QColor("#344a7e"))
        painter.drawEllipse(QPointF(13, 18), 1.2, 1.2)
