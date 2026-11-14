"""Dock geometry in Qt logical pixels and animated island transitions."""
from time import perf_counter
from examai.performance import timing

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QRect, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import QSizeGrip, QSizePolicy


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
        self.collapsed = self.docked
        self.animation = QPropertyAnimation(window, b"geometry", self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.finished.connect(self.animation_finished)
        self.started = 0
        self.frames = 0
        self.transition_ms = None
        self.animation.valueChanged.connect(self.frame)
        self.card_animation = QPropertyAnimation(window, b"geometry", self)
        self.card_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.card_animation.finished.connect(self.finish_cards)
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
        self.collapsed = self.docked
        self.apply()

    def expand_toggle(self):
        if not self.docked:
            return
        self.collapsed = not self.collapsed
        self.apply(animate=True)

    def recover(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        self.config.save_config({"window_mode": "docked", "dock_screen": screen.name()})
        self.docked = True
        self.collapsed = True
        self.window.showNormal()
        self.apply()
        self.window.raise_()
        self.window.activateWindow()

    def update_pin_label(self):
        self.window.btn_pin.setText(self.window.tr.local("Відкріпити" if self.docked else "Закріпити"))

    def apply(self, animate=False):
        w = self.window
        self.animation.stop()
        self.card_animation.stop()
        w.live_cards.hide()
        w.header_widget.setMinimumHeight(0)
        w.header_widget.setMaximumHeight(16777215)
        w.main_layout.setSpacing(15)
        if not self.docked:
            self.collapsed = False
        self.update_pin_label()
        w.btn_island.setVisible(self.docked)
        w.btn_island.setText("▾" if self.collapsed else "▴")
        for widget in w.expanded_header_widgets:
            widget.setVisible(not self.collapsed)
        for widget in w.compact_widgets:
            widget.setVisible(self.collapsed)
        header = w.header_widget
        header.setObjectName("CompactHeader" if self.collapsed else "")
        for button in (w.btn_pin, w.btn_island, w.compact_listen, w.compact_screen):
            button.style().unpolish(button)
            button.style().polish(button)
        w.header_spacer.changeSize(0, 0, QSizePolicy.Policy.Fixed if self.collapsed
                                   else QSizePolicy.Policy.Expanding)
        header.layout().invalidate()
        header.layout().setStretch(w.header_stretch, 0 if self.collapsed else 1)
        margins = (12, 7, 12, 7) if self.collapsed else (18, 16, 18, 16)
        w.main_layout.setContentsMargins(*margins)
        for widget in (w.sidebar, w.tabs, w.footer_widget):
            widget.hide()
        w.setMinimumSize(0, 0)
        self.grip.setVisible(not self.docked)
        topmost = self.docked or self.config.get("always_on_top", True)
        if bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) != topmost:
            visible = w.isVisible()
            w.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, topmost)
            if visible:
                w.show()

        screen = self.screen()
        if screen is None:
            self.finish_layout()
            return
        area = screen.availableGeometry()
        target = dock_geometry(area, self.config.get("dock_anchor"), 940, 54 if self.collapsed else 620)
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
            self.finish_layout()

    def finish_layout(self):
        w = self.window
        if not self.collapsed:
            w.setMinimumSize(640, 440)
            for widget in (w.sidebar, w.tabs, w.footer_widget):
                widget.show()
        w.main_layout.activate()
        w.refresh_compact_status()
        self.fit_cards()

    def fit_cards(self, animate=False):
        w = self.window
        if not self.docked or not self.collapsed or self.animation.state() == QPropertyAnimation.State.Running:
            return
        screen = self.screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        base = dock_geometry(area, self.config.get("dock_anchor"), 940, 54)
        cards = w.live_cards
        extra = cards.height_for(area.height() - base.height())
        if extra:
            w.main_layout.setSpacing(6)
            cards.show()
            w.header_widget.setFixedHeight(40)
        else:
            cards.hide()
        w.main_layout.activate()
        w.setMinimumSize(0, 0)
        target = base.adjusted(0, 0, 0, extra)
        if self.card_animation.state() == QPropertyAnimation.State.Running:
            if self.card_animation.endValue() == target:
                return
            if animate:
                cards.schedule_fit()
                return
        self.card_animation.stop()
        if animate and w.isVisible() and w.motion_ms(180) and target != w.geometry():
            self.card_animation.setDuration(w.motion_ms(180))
            self.card_animation.setStartValue(w.geometry())
            self.card_animation.setEndValue(target)
            self.card_animation.start()
        elif target != w.geometry():
            w.setGeometry(target)
        self.finish_cards()

    def finish_cards(self):
        self.window.main_layout.activate()

    def frame(self, _geometry):
        self.frames += 1

    def animation_finished(self):
        self.finish_layout()
        duration = perf_counter() - self.started
        self.transition_ms = duration * 1000
        if hasattr(self.window, "performance_monitor") and duration:
            fps = max(0, self.frames - 1) / duration
            self.window.performance_monitor.fps = fps
            timing.info("%s_ms=%.1f animation_fps=%.1f",
                        "collapse" if self.collapsed else "expand", self.transition_ms, fps)
