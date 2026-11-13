"""Dock geometry in Qt logical pixels and animated island transitions."""
from time import perf_counter
from performance import timing

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QRect, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QSizeGrip


def dock_geometry(area, anchor, width, height):
    width = min(width, area.width())
    height = min(height, area.height())
    gap = min(12, (area.width() - width) // 2)
    x = {"left": area.left() + gap, "right": area.right() - width + 1 - gap}.get(
        anchor, area.left() + (area.width() - width) // 2)
    return QRect(x, area.top(), width, height)


class WindowMode(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.config = window.config_mgr
        self.docked = self.config.get("window_mode") != "floating"
        self.collapsed = False
        self.animation = QPropertyAnimation(window, b"geometry", self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.finished.connect(self.animation_finished)
        self.started = 0
        self.frames = 0
        self.transition_ms = None
        self.animation.valueChanged.connect(self.frame)
        self.grip = QSizeGrip(window)
        window.footer.addWidget(self.grip)
        self.apply()

    def enable_local_recovery(self):
        self.recover_key = QShortcut(QKeySequence("Ctrl+Shift+F12"), self.window)
        self.recover_key.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.recover_key.activated.connect(self.recover)

    def screen(self):
        name = self.config.get("dock_screen")
        return next((screen for screen in QGuiApplication.screens() if screen.name() == name),
                    QGuiApplication.primaryScreen())

    def remember(self):
        if not self.docked and not self.window.isMinimized():
            g = self.window.geometry()
            self.config.set("floating_geometry", [g.x(), g.y(), g.width(), g.height()])

    def toggle(self):
        self.remember()
        if not self.docked and self.window.screen() is not None:
            self.config.set("dock_screen", self.window.screen().name())
        self.docked = not self.docked
        self.config.set("window_mode", "docked" if self.docked else "floating")
        self.collapsed = False
        self.apply()

    def expand_toggle(self):
        self.collapsed = not self.collapsed
        self.apply(animate=True)

    def recover(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        self.config.save_config({"window_mode": "docked", "dock_screen": screen.name()})
        self.docked = True
        self.collapsed = False
        self.window.showNormal()
        self.apply()
        self.window.raise_()
        self.window.activateWindow()

    def apply(self, animate=False):
        w = self.window
        self.animation.stop()
        if not self.docked:
            self.collapsed = False
        w.btn_pin.setText("Відкріпити" if self.docked else "Закріпити")
        w.btn_island.setVisible(self.docked)
        w.btn_island.setText("▾" if self.collapsed else "▴")
        for widget in (w.sidebar, w.tabs, w.footer_widget):
            widget.setVisible(not self.collapsed)
        self.grip.setVisible(not self.docked)
        w.setMinimumSize(0, 0) if self.collapsed else w.setMinimumSize(640, 440)
        topmost = self.docked or self.config.get("always_on_top", True)
        if bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) != topmost:
            visible = w.isVisible()
            w.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, topmost)
            if visible:
                w.show()

        screen = self.screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        target = dock_geometry(area, self.config.get("dock_anchor"), 940, 76 if self.collapsed else 620)
        if not self.docked:
            saved = self.config.get("floating_geometry", [])
            if len(saved) == 4 and all(type(value) is int for value in saved):
                candidate = QRect(*saved)
                if candidate.isValid() and any(
                    display.availableGeometry().intersected(candidate).width() >= 200
                    and display.availableGeometry().contains(candidate.topLeft())
                    for display in QGuiApplication.screens()
                ):
                    target = candidate
        if animate and w.motion_ms(260):
            self.started = perf_counter()
            self.frames = 0
            self.animation.setDuration(w.motion_ms(260))
            self.animation.setStartValue(w.geometry())
            self.animation.setEndValue(target)
            self.animation.start()
        else:
            w.setGeometry(target)

    def frame(self, _geometry):
        self.frames += 1

    def animation_finished(self):
        duration = perf_counter() - self.started
        self.transition_ms = duration * 1000
        if hasattr(self.window, "performance_monitor") and duration:
            fps = max(0, self.frames - 1) / duration
            self.window.performance_monitor.fps = fps
            timing.info("%s_ms=%.1f animation_fps=%.1f",
                        "collapse" if self.collapsed else "expand", self.transition_ms, fps)
