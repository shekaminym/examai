import sqlite3
import math
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QComboBox, QTabWidget, QFileDialog, QProgressBar, QLineEdit,
    QListWidget, QGraphicsOpacityEffect, QCheckBox, QSizePolicy, QScrollArea, QFrame, QDialog, QStackedWidget, QApplication, QColorDialog, QSpinBox
)
from PySide6.QtCore import QEvent, Qt, Signal, Slot, QTimer, QPropertyAnimation, QEasingCurve, QSize, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QPainter, QColor, QPen, QPixmap, QIcon, QLinearGradient, QPainterPath
from audio_capturer import AudioCapturer
from response_view import ResponseView
from translator import Translator
from appearance import THEMES, blend, set_windows_backdrop, stylesheet
from performance import LEGACY, PROFILES, PerformanceMonitor, RuntimePolicy, detect_hardware, recommend
from window_mode import WindowMode
from live_cards import LiveCards

from branding import VERSION, REPOSITORY

from rounded_window import configure_rounded_window
from ghost_controls import GhostToggle, GhostLockButton


def navigation_icon(name):
    """Small line icons rendered locally so Windows font fallbacks cannot hide them."""
    pixmap = QPixmap(22, 22)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#c4d6ff"), 1.8, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if name == "live":
        painter.drawLine(3, 10, 11, 3)
        painter.drawLine(11, 3, 19, 10)
        painter.drawLine(5, 9, 5, 19)
        painter.drawLine(17, 9, 17, 19)
        painter.drawLine(5, 19, 17, 19)
    elif name == "docs":
        painter.drawRoundedRect(4, 2, 14, 18, 2, 2)
        for y in (8, 12, 16):
            painter.drawLine(7, y, 15, y)
    elif name == "settings":
        painter.drawEllipse(5, 5, 12, 12)
        painter.drawEllipse(9, 9, 4, 4)
        for index in range(8):
            angle = index * math.pi / 4
            painter.drawLine(int(11 + 7 * math.cos(angle)),
                             int(11 + 7 * math.sin(angle)),
                             int(11 + 10 * math.cos(angle)),
                             int(11 + 10 * math.sin(angle)))
    elif name == "about":
        painter.drawEllipse(3, 3, 16, 16)
        painter.drawPoint(11, 7)
        painter.drawLine(11, 10, 11, 16)
    elif name == "subscriptions":
        painter.drawRoundedRect(3, 5, 16, 12, 3, 3)
        painter.drawLine(6, 10, 16, 10)
    else:
        painter.drawEllipse(3, 3, 16, 16)
        painter.drawLine(11, 11, 11, 6)
        painter.drawLine(11, 11, 15, 13)
    painter.end()
    return QIcon(pixmap)


class MinimalComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(8)
        self.setMinimumWidth(0)

    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#c4d8ff"), 1.8, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        x, y = self.width() - 17, self.height() / 2
        painter.drawLine(int(x - 4), int(y - 2), int(x), int(y + 2))
        painter.drawLine(int(x), int(y + 2), int(x + 4), int(y - 2))


class SendButton(QPushButton):
    def __init__(self):
        super().__init__("")
        self.setAccessibleName("Надіслати питання")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor("#6c96ed" if not self.isDown() else "#4d6fba"))
        gradient.setColorAt(1, QColor("#795bda" if not self.isDown() else "#5e48a9"))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor("#9fb9f7"), 1))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 18, 18)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#f7faff"), 2.1, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        center_x, center_y = self.width() / 2, self.height() / 2
        path = QPainterPath()
        path.moveTo(center_x - 8, center_y + 6)
        path.lineTo(center_x + 8, center_y)
        path.lineTo(center_x - 8, center_y - 6)
        path.moveTo(center_x + 8, center_y)
        path.lineTo(center_x - 2, center_y)
        painter.drawPath(path)


class HistoryButton(QPushButton):
    def __init__(self, direction):
        super().__init__("")
        self.direction = direction

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(1.0 if self.isEnabled() else 0.38)
        painter.setBrush(QColor("#304c82" if not self.isDown() else "#4565a1"))
        painter.setPen(QPen(QColor("#5f7db5"), 1))
        painter.drawEllipse(self.rect().adjusted(1, 1, -2, -2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#e1ecff"), 1.8, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        x, y = self.width() / 2, self.height() / 2
        tip = x + (3 if self.direction > 0 else -3)
        outer = x - (3 if self.direction > 0 else -3)
        painter.drawLine(int(outer), int(y - 5), int(tip), int(y))
        painter.drawLine(int(tip), int(y), int(outer), int(y + 5))


class RecordingButton(QPushButton):
    def __init__(self, text):
        super().__init__(text)
        self.recording = False
        self.animation_enabled = True
        self.phase = 0.0
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(60)
        self.pulse_timer.timeout.connect(self.advance_pulse)

    def set_recording(self, recording):
        self.recording = bool(recording)
        if self.recording and self.animation_enabled:
            self.pulse_timer.start()
        else:
            self.pulse_timer.stop()
        self.update()

    def set_animation_enabled(self, enabled):
        self.animation_enabled = bool(enabled)
        self.set_recording(self.recording)

    def advance_pulse(self):
        self.phase += 0.22
        self.update()

    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.recording:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        pulse = (math.sin(self.phase) + 1) / 2 if self.animation_enabled else 0.5
        painter.setBrush(QColor(255, 129, 164, int(90 + pulse * 75)))
        painter.drawEllipse(19, self.height() // 2 - 10, 20, 20)
        painter.setBrush(QColor("#ffd4e1"))
        painter.drawEllipse(25, self.height() // 2 - 4, 8, 8)


class AudioVisualizer(QWidget):
    """Compact five-bar audio indicator."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(42, 19)
        self.target_rms = 0.0
        self.current_rms = 0.0
        self._phase = 0.0
        self._active = False
        self._mode = "visual"
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_animation)

    def set_performance_mode(self, mode):
        self._mode = mode
        self.set_active(self._active)

    def set_active(self, active):
        self._active = bool(active)
        if not self._active or self._mode == "performance":
            self.timer.stop()
            self.current_rms = 0.0
            self.update()
        else:
            self.timer.start(20 if self._mode == "visual" else 50)

    def set_rms(self, val):
        self.target_rms = val

    def update_animation(self):
        self.current_rms += (self.target_rms - self.current_rms) * 0.15
        self._phase += 0.08
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        level = min(1.0, self.current_rms * 9)
        for index in range(5):
            motion = abs(math.sin(self._phase + index * 0.7)) if level else 0.0
            height = 4 + int(12 * level * motion)
            painter.setBrush(QColor("#91c4ff" if index < 3 else "#bda6ff"))
            painter.drawRoundedRect(4 + index * 8, (19 - height) // 2, 4, height, 2, 2)


class StatusLabel(QLabel):
    text_changed = Signal(str)

    def setText(self, text):
        super().setText(text)
        self.text_changed.emit(text)


class MainWindow(QMainWindow):
    close_requested = Signal()
    question_submitted = Signal(str)
    screen_select_signal = Signal()
    clear_all_signal = Signal()
    start_listening_signal = Signal()
    stop_listening_signal = Signal()
    file_uploaded_signal = Signal(str)
    file_deleted_signal = Signal(str)
    ghost_toggled_signal = Signal(bool)
    search_keyword_signal = Signal(str)
    auto_summary_signal = Signal(str)
    history_step_signal = Signal(int)
    history_selected_signal = Signal(int)
    developer_timing_signal = Signal()

    def __init__(self, config_mgr, db_mgr):
        super().__init__()
        self.config_mgr = config_mgr
        self.db_mgr = db_mgr
        
        saved_lang = self.config_mgr.get("language", "UA")
        self.tr = Translator(saved_lang)

        self.hardware = detect_hardware(QGuiApplication.primaryScreen())
        self.performance_policy = RuntimePolicy(recommend(self.hardware))
        self.profile = self.performance_policy.profile
        self.setWindowTitle("EXAM AI")
        self.setObjectName("ExamAIWindow")
        self.resize(940, 620)
        self.setMinimumSize(860, 550)

        self.update_window_flags()
        configure_rounded_window(
            self, radius=24, opacity=1.0,
            color="#0a173b",
        )

        self.init_ui()
        self.apply_aurora_theme()
        self.apply_performance_mode(self.config_mgr.get("ui_performance_mode"))
        self.window_mode = WindowMode(self)
        self.btn_pin.clicked.connect(self.window_mode.toggle)
        self.btn_island.clicked.connect(self.window_mode.expand_toggle)
        self.performance_monitor = PerformanceMonitor(self, self.performance_policy, self.on_performance_sample)
        app = QGuiApplication.instance()
        app.screenRemoved.connect(self.on_screen_removed)
        app.screenAdded.connect(self.watch_screen_geometry)
        for screen in app.screens():
            self.watch_screen_geometry(screen)
        self.refresh_audio_devices()
        self.refresh_documents_list()
        self.retranslate_ui()

    def update_window_flags(self):
        flags = Qt.Window | Qt.FramelessWindowHint
        if self.config_mgr.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def init_ui(self):
        self.central_container = QWidget(self)
        self.central_container.setObjectName("CentralContainer")
        self.setCentralWidget(self.central_container)

        main_layout = QVBoxLayout(self.central_container)
        self.main_layout = main_layout
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(15)

        # Header
        top_bar = QHBoxLayout()
        top_bar.setSpacing(4)

        self.lbl_brand_icon = QLabel("✦")
        self.lbl_brand_icon.setObjectName("BrandIcon")
        self.lbl_brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_brand_icon.setFixedSize(38, 38)

        lang_container = QWidget()
        lang_container.setObjectName("LangContainer")
        lang_layout = QHBoxLayout(lang_container)
        lang_layout.setContentsMargins(3, 3, 3, 3)
        lang_layout.setSpacing(2)

        self.btn_ua = QPushButton("UA")
        self.btn_ua.setObjectName("LangBtnActive" if self.tr.current_lang == "UA" else "LangBtn")
        self.btn_ua.setFixedSize(32, 24)
        self.btn_ua.clicked.connect(lambda: self.change_language("UA"))

        self.btn_eng = QPushButton("ENG")
        self.btn_eng.setObjectName("LangBtnActive" if self.tr.current_lang == "ENG" else "LangBtn")
        self.btn_eng.setFixedSize(38, 24)
        self.btn_eng.clicked.connect(lambda: self.change_language("ENG"))

        lang_layout.addWidget(self.btn_ua)
        lang_layout.addWidget(self.btn_eng)

        self.btn_ghost = GhostToggle()
        self.btn_ghost.setObjectName("GhostToggle")
        self.btn_ghost.setCheckable(True)
        self.set_privacy_button(False)
        self.btn_ghost.clicked.connect(self.on_ghost_toggled)
        self.btn_ghost.blocked.connect(lambda: self.show_toast("Ghost Mode заблоковано — відкрийте замок"))
        self.btn_ghost_lock = GhostLockButton()
        self.btn_ghost_lock.setChecked(self.config_mgr.get("ghost_controls_locked", False))
        self.btn_ghost_lock.toggled.connect(self.on_ghost_lock_changed)
        self.btn_ghost.set_locked(self.btn_ghost_lock.isChecked())
        self.btn_ghost_lock.setToolTip("Замок: захист від випадкового перемикання Ghost Mode")

        btn_min = QPushButton("—")
        btn_min.setFixedSize(26, 26)
        btn_min.setObjectName("WindowBtn")
        btn_min.clicked.connect(self.showMinimized)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(26, 26)
        btn_close.setObjectName("CloseBtn")
        btn_close.clicked.connect(self.close)

        self.lbl_ai_provider = QLabel("⚡ EXAM AI")
        self.lbl_ai_provider.setObjectName("HeaderPill")
        self.lbl_ai_provider.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_ai_provider.setFixedHeight(32)
        self.answer_mode = MinimalComboBox()
        self.answer_mode.setObjectName("HeaderMode")
        self.answer_mode.addItem("FAST", "fast")
        self.answer_mode.addItem("SMART", "smart")
        self.answer_mode.setCurrentIndex(1 if self.config_mgr.get("answer_mode") == "smart" else 0)
        self.answer_mode.currentIndexChanged.connect(self.save_answer_mode)

        self.expanded_header_widgets = (self.lbl_brand_icon, lang_container, self.btn_ghost,
                                        self.btn_ghost_lock, btn_min, btn_close)
        top_bar.addWidget(self.lbl_brand_icon)
        top_bar.addSpacing(8)
        top_bar.addSpacing(24)
        top_bar.addWidget(lang_container)
        self.header_stretch = top_bar.count()
        top_bar.addStretch()
        self.header_spacer = top_bar.itemAt(self.header_stretch).spacerItem()
        top_bar.addWidget(self.lbl_ai_provider)
        self.compact_status = QLabel()
        self.compact_status.setObjectName("StatusLabel")
        self.compact_status.installEventFilter(self)
        self.compact_status.setMinimumWidth(96)
        self.compact_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top_bar.addWidget(self.compact_status, 1)
        self.compact_listen = QPushButton()
        self.compact_listen.clicked.connect(self.on_toggle_listen)
        self.compact_screen = QPushButton()
        self.compact_screen.clicked.connect(self.screen_select_signal.emit)
        self.compact_settings = QPushButton("⚙")
        self.compact_settings.setToolTip("Налаштування")
        self.compact_settings.setAccessibleName("Налаштування")
        self.compact_settings.setFixedSize(29, 30)
        self.compact_settings.clicked.connect(self.open_settings)
        self.compact_widgets = (self.compact_status, self.compact_listen, self.compact_screen,
                                self.compact_settings)
        for button in (self.compact_listen, self.compact_screen):
            button.setMinimumHeight(32)
            top_bar.addWidget(button)
        top_bar.addSpacing(7)
        top_bar.addWidget(self.answer_mode)
        top_bar.addWidget(self.compact_settings)
        top_bar.addSpacing(8)
        top_bar.addWidget(self.btn_ghost)
        top_bar.addWidget(self.btn_ghost_lock)
        top_bar.addSpacing(6)
        self.btn_pin = QPushButton("Відкріпити")
        self.btn_pin.setMinimumHeight(28)
        self.btn_island = QPushButton("▴")
        self.btn_island.setFixedSize(28, 28)
        self.btn_island.setToolTip("Згорнути або розгорнути панель")
        top_bar.addWidget(self.btn_pin)
        top_bar.addWidget(self.btn_island)
        top_bar.addWidget(btn_min)
        top_bar.addWidget(btn_close)
        self.header_widget = QWidget()
        self.header_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.header_widget.setLayout(top_bar)
        top_bar.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.header_widget)
        self.live_cards = LiveCards(self)
        main_layout.addWidget(self.live_cards)

        # Navigation and pages
        body = QHBoxLayout()
        body.setSpacing(18)
        self.sidebar = QWidget()
        self.sidebar.setObjectName("SideBar")
        self.sidebar.setFixedWidth(178)
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(10, 15, 10, 12)
        side_layout.setSpacing(8)
        self.nav_buttons = {}
        for name, label in (
            ("live", "AI"),
            ("docs", "Конспекти"),
            ("settings", "Налаштування"),
            ("history", "Історія"),
        ):
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setFixedHeight(45)
            button.setIcon(navigation_icon(name))
            button.setIconSize(QSize(19, 19))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            side_layout.addWidget(button)
            self.nav_buttons[name] = button
        side_layout.addStretch()
        for name, label in (("about", "Про нас"), ("subscriptions", "Підписки")):
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setFixedHeight(42)
            button.setIcon(navigation_icon(name))
            button.setIconSize(QSize(19, 19))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            side_layout.addWidget(button)
            self.nav_buttons[name] = button

        self.tabs = QTabWidget()
        self.tabs.tabBar().hide()
        body.addWidget(self.sidebar)
        body.addWidget(self.tabs, 1)
        main_layout.addLayout(body, 1)

        self.fade_effect = QGraphicsOpacityEffect(self.tabs)
        self.tabs.setGraphicsEffect(self.fade_effect)
        self.fade_anim = QPropertyAnimation(self.fade_effect, b"opacity")
        self.fade_anim.setDuration(220)
        self.fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade_anim.finished.connect(lambda: self.fade_effect.setEnabled(False))
        self.tabs.currentChanged.connect(self.animate_tab_change)

        self.tab_live = QWidget()
        self.init_live_tab()
        self.btn_toggle_listen.installEventFilter(self)
        self.btn_select_screen.installEventFilter(self)
        self.tabs.addTab(self.tab_live, "AI")

        self.init_settings_window()
        self.nav_buttons["settings"].clicked.connect(self.open_settings)

        self.tab_docs = QWidget()
        self.init_docs_tab()
        self.tabs.addTab(self.tab_docs, "Конспекти")

        self.tab_history = QWidget()
        history_layout = QVBoxLayout(self.tab_history)
        history_layout.setContentsMargins(0, 8, 0, 0)
        history_layout.addWidget(QLabel("ІСТОРІЯ ВІДПОВІДЕЙ"))
        self.history_list = QListWidget()
        self.history_list.setObjectName("HistoryList")
        self.history_list.itemClicked.connect(
            lambda item: self.history_selected_signal.emit(self.history_list.row(item))
        )
        history_layout.addWidget(self.history_list)
        self.tabs.addTab(self.tab_history, "Історія")
        self.tab_about = QWidget()
        self.init_about_tab()
        self.tabs.addTab(self.tab_about, "Про нас")
        self.tab_subscriptions = QWidget()
        self.tabs.addTab(self.tab_subscriptions, "Підписки")
        for name, page in (("live", self.tab_live), ("docs", self.tab_docs),
                           ("history", self.tab_history),
                           ("about", self.tab_about), ("subscriptions", self.tab_subscriptions)):
            self.nav_buttons[name].clicked.connect(lambda _checked=False, target=page:
                                                    self.tabs.setCurrentWidget(target))
        self.tabs.currentChanged.connect(self.update_nav_selection)
        self.update_nav_selection(0)

        # Full-width footer keeps long status messages readable.
        self.footer_widget = QWidget()
        status_bar = QHBoxLayout(self.footer_widget)
        status_bar.setContentsMargins(0, 0, 0, 0)
        self.footer = status_bar
        status_bar.setSpacing(8)
        self.lbl_status = StatusLabel("Готовий")
        self.lbl_status.text_changed.connect(self.set_compact_status)
        self.lbl_status.setObjectName("StatusLabel")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setMinimumHeight(32)
        self.lbl_status.setMaximumHeight(58)
        
        self.audio_visualizer = AudioVisualizer()
        status_bar.addWidget(self.lbl_status, 1)
        status_bar.addWidget(self.audio_visualizer, alignment=Qt.AlignmentFlag.AlignVCenter)
        main_layout.addWidget(self.footer_widget)

        self.lbl_toast = QLabel(self)
        self.lbl_toast.setObjectName("ToastLabel")
        self.lbl_toast.setAlignment(Qt.AlignCenter)
        self.lbl_toast.hide()

    def change_language(self, lang):
        if self.tr.current_lang == lang:
            return
        
        self.tr.set_language(lang)
        try:
            self.config_mgr.set("language", lang)
        except OSError:
            pass

        self.btn_ua.setObjectName("LangBtnActive" if lang == "UA" else "LangBtn")
        self.btn_eng.setObjectName("LangBtnActive" if lang == "ENG" else "LangBtn")
        self.btn_ua.style().unpolish(self.btn_ua)
        self.btn_ua.style().polish(self.btn_ua)
        self.btn_eng.style().unpolish(self.btn_eng)
        self.btn_eng.style().polish(self.btn_eng)

        self.retranslate_ui()
        self.show_toast(self.tr.get("toast_changed"))

    def show_toast(self, message):
        if not hasattr(self, "_toast_timer"):
            self.toast_opacity = QGraphicsOpacityEffect(self.lbl_toast)
            self.lbl_toast.setGraphicsEffect(self.toast_opacity)
            self.toast_anim_in = QPropertyAnimation(self.toast_opacity, b"opacity", self)
            self.toast_anim_in.setDuration(160)
            self.toast_anim_out = QPropertyAnimation(self.toast_opacity, b"opacity", self)
            self.toast_anim_out.setDuration(160)
            self.toast_anim_out.setEndValue(0.0)
            self.toast_anim_out.finished.connect(self.lbl_toast.hide)
            self._toast_timer = QTimer(self)
            self._toast_timer.setSingleShot(True)
            self._toast_timer.timeout.connect(self.hide_toast)
        self.toast_anim_in.stop()
        self.toast_anim_out.stop()
        self.lbl_toast.setText(message)
        self.lbl_toast.adjustSize()
        
        w = min(self.width() - 28, max(200, self.lbl_toast.width() + 30))
        h = 36
        self.lbl_toast.setGeometry((self.width() - w) // 2, self.height() - 75, w, h)
        self.lbl_toast.show()
        self.lbl_toast.raise_()
        if not self.motion_ms(160):
            self.toast_opacity.setOpacity(1.0)
            self._toast_timer.start(1800)
            return
        self.toast_anim_in.setDuration(self.motion_ms(160))
        self.toast_anim_in.setStartValue(0.0)
        self.toast_anim_in.setEndValue(1.0)
        self.toast_anim_in.start()

        self._toast_timer.start(1800)

    def hide_toast(self):
        if not self.motion_ms(160):
            self.lbl_toast.hide()
            return
        self.toast_anim_in.stop()
        self.toast_anim_out.setDuration(self.motion_ms(160))
        self.toast_anim_out.setStartValue(self.toast_opacity.opacity())
        self.toast_anim_out.start()

    def retranslate_ui(self):
        self.tabs.setTabText(0, self.tr.get("tab_ai"))
        self.tabs.setTabText(1, self.tr.get("tab_docs"))
        self.tabs.setTabText(2, "Історія" if self.tr.current_lang == "UA" else "History")
        self.tabs.setTabText(3, "Про нас" if self.tr.current_lang == "UA" else "About us")
        self.tabs.setTabText(4, "Підписки" if self.tr.current_lang == "UA" else "Subscriptions")
        labels = {
            "live": "AI",
            "docs": self.tr.get("tab_docs"),
            "settings": self.tr.get("tab_settings"),
            "history": "Історія" if self.tr.current_lang == "UA" else "History",
            "about": "Про нас" if self.tr.current_lang == "UA" else "About us",
            "subscriptions": "Підписки" if self.tr.current_lang == "UA" else "Subscriptions",
        }
        for name, button in self.nav_buttons.items():
            button.setText(labels[name])
        
        is_listening = self.btn_toggle_listen.text() in ["СТОП", "STOP"]
        self.btn_toggle_listen.setText(self.tr.get("btn_stop") if is_listening else self.tr.get("btn_listen"))
        self.compact_listen.setText(self.btn_toggle_listen.text())
        self.compact_screen.setText("Екран" if self.tr.current_lang == "UA" else "Screen")
        self.compact_screen.setToolTip(self.tr.get("btn_select_screen"))
        self.btn_select_screen.setText(self.tr.get("btn_select_screen"))
        self.btn_toggle_listen.setMinimumWidth(0)
        self.btn_select_screen.setMinimumWidth(
            self.btn_select_screen.fontMetrics().horizontalAdvance(self.btn_select_screen.text()) + 20
        )
        self.lbl_q_title.setText(self.tr.get("lbl_question"))
        self.btn_clear_all.setText(self.tr.get("btn_clear_all"))
        self.auto_send.setText(self.tr.get("auto_send"))
        self.btn_send_q.setToolTip(self.tr.get("btn_send"))
        self.input_manual_question.setPlaceholderText(
            "Поставити питання…" if self.tr.current_lang == "UA" else "Ask a question…"
        )
        self.lbl_a_title.setText(self.tr.get("lbl_answer"))
        self.lbl_status.setText(self.tr.get("status_ready"))
        
        self.lbl_set_gemini.setText(self.tr.get("set_gemini"))
        self.lbl_set_hint.setText(self.tr.get("set_keys_hint"))
        self.lbl_stt_plan.setText(self.tr.get("set_stt_plan"))
        self.lbl_set_model.setText(self.tr.get("set_model"))
        self.lbl_set_source.setText(self.tr.get("set_audio_source"))
        self.lbl_set_device.setText(self.tr.get("set_audio_device"))
        self.btn_save_cfg.setText("Зберегти" if self.tr.current_lang == "UA" else "Save")
        self.btn_open_timing.setText("ВІКНО ЗАМІРІВ" if self.tr.current_lang == "UA" else "TIMING WINDOW")
        
        self.btn_upload.setText(self.tr.get("btn_add_doc"))
        self.input_search.setPlaceholderText(self.tr.get("search_placeholder"))
        self.btn_search.setText(self.tr.get("btn_search"))
        self.btn_summary.setText(self.tr.get("btn_summary"))
        self.btn_delete_doc.setText(self.tr.get("btn_del_doc"))
        self.txt_docs_result.setPlaceholderText(self.tr.get("docs_placeholder"))
        
        self.set_privacy_button(
            self._privacy_verified, self._privacy_requested, self._privacy_error,
        )

    def animate_tab_change(self, index):
        if not self.motion_ms(160):
            self.fade_effect.setOpacity(1.0)
            return
        self.fade_anim.stop()
        self.fade_anim.setStartValue(0.65)
        self.fade_anim.setEndValue(1.0)
        self.fade_effect.setEnabled(True)
        self.fade_anim.start()

    def update_nav_selection(self, _index):
        current = self.tabs.currentWidget()
        for name, page in (("live", self.tab_live), ("docs", self.tab_docs),
                           ("history", self.tab_history),
                           ("about", self.tab_about), ("subscriptions", self.tab_subscriptions)):
            button = self.nav_buttons[name]
            button.setObjectName("NavButtonActive" if current is page else "NavButton")
            button.style().unpolish(button)
            button.style().polish(button)

    def set_history_items(self, history):
        self.history_list.clear()
        for _request_id, question, _answer in history:
            self.history_list.addItem(question[:120] or "Питання без тексту")

    def init_live_tab(self):
        layout = QVBoxLayout(self.tab_live)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        ctrl_box = QHBoxLayout()
        self.btn_toggle_listen = RecordingButton("СЛУХАТИ")
        self.btn_toggle_listen.setFixedHeight(62)
        self.btn_toggle_listen.setObjectName("BtnListenStart")
        self.btn_toggle_listen.clicked.connect(self.on_toggle_listen)

        self.btn_select_screen = QPushButton(self.tr.get("btn_select_screen"))
        self.btn_select_screen.setFixedHeight(62)
        self.btn_select_screen.setObjectName("BtnPrimary")
        self.btn_select_screen.clicked.connect(self.screen_select_signal.emit)

        ctrl_box.addWidget(self.btn_toggle_listen, stretch=1)
        ctrl_box.addWidget(self.btn_select_screen, stretch=1)
        layout.addLayout(ctrl_box)

        q_header = QHBoxLayout()
        self.lbl_q_title = QLabel("Питання")
        self.lbl_q_title.setObjectName("HeaderLabel")
        self.btn_clear_all = QPushButton("Очистити все")
        self.btn_clear_all.setObjectName("SmallBtn")
        self.btn_clear_all.clicked.connect(self.clear_both)
        
        q_header.addWidget(self.lbl_q_title)
        q_header.addStretch()
        q_header.addWidget(self.btn_clear_all)
        layout.addLayout(q_header)

        self.txt_question = QTextEdit()
        self.txt_question.setAcceptRichText(False)
        self.txt_question.setPlaceholderText("Очікування мовлення...")
        self.txt_question.setObjectName("QuestionEditor")
        self.txt_question.setFixedHeight(86)
        layout.addWidget(self.txt_question)

        question_controls = QHBoxLayout()
        self.auto_send = QCheckBox("Автоматично надсилати з аудіо")
        self.auto_send.setChecked(self.config_mgr.get("auto_send", True))
        self.auto_send.toggled.connect(self.save_auto_send)
        question_controls.addWidget(self.auto_send)
        question_controls.addStretch()
        
        layout.addLayout(question_controls)

        a_header = QHBoxLayout()
        self.lbl_a_title = QLabel("✦  Відповідь AI (Gemini)")
        self.lbl_a_title.setObjectName("HeaderLabelActive")
        a_header.addWidget(self.lbl_a_title)
        a_header.addStretch()
        self.btn_history_prev = HistoryButton(-1)
        self.btn_history_next = HistoryButton(1)
        for button in (self.btn_history_prev, self.btn_history_next):
            button.setObjectName("SmallBtn")
            button.setFixedSize(28, 26)
            button.setEnabled(False)
        self.btn_history_prev.setToolTip("Попередня відповідь")
        self.btn_history_next.setToolTip("Наступна відповідь")
        self.btn_history_prev.clicked.connect(lambda: self.history_step_signal.emit(-1))
        self.btn_history_next.clicked.connect(lambda: self.history_step_signal.emit(1))
        self.lbl_history_position = QLabel("0/0")
        self.lbl_history_position.setObjectName("HeaderLabel")
        a_header.addWidget(self.btn_history_prev)
        a_header.addWidget(self.lbl_history_position)
        a_header.addWidget(self.btn_history_next)
        layout.addLayout(a_header)

        self.txt_answer = ResponseView()
        self.txt_answer.setObjectName("AnswerView")
        layout.addWidget(self.txt_answer)

        composer = QHBoxLayout()
        self.input_manual_question = QLineEdit()
        self.input_manual_question.setObjectName("ComposerInput")
        self.input_manual_question.setPlaceholderText("Поставити питання…")
        self.input_manual_question.returnPressed.connect(self.submit_composer_question)
        self.btn_send_q = SendButton()
        self.btn_send_q.setObjectName("ComposerSend")
        self.btn_send_q.setToolTip("Надіслати питання")
        self.btn_send_q.setFixedSize(48, 46)
        self.btn_send_q.clicked.connect(self.submit_composer_question)
        composer.addWidget(self.input_manual_question, 1)
        composer.addWidget(self.btn_send_q)
        layout.addLayout(composer)

    def submit_composer_question(self):
        typed = self.input_manual_question.text().strip()
        self.question_submitted.emit(typed or self.txt_question.toPlainText())
        if typed:
            self.input_manual_question.clear()

    def open_settings(self):
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def init_settings_window(self):
        window = self.settings_window = QDialog(self)
        window.setWindowTitle("EXAM AI · Налаштування")
        window.setObjectName("SettingsWindow")
        window.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        window.resize(720, 490)
        window.setSizeGripEnabled(True)
        window.setMinimumSize(410, 300)
        root = QVBoxLayout(window)
        body = QHBoxLayout()
        root.addLayout(body, 1)
        self.settings_navigation = QListWidget()
        self.settings_navigation.setFixedWidth(156)
        self.settings_navigation.setWordWrap(True)
        body.addWidget(self.settings_navigation)
        pages = QStackedWidget()
        body.addWidget(pages, 1)
        groups = {}
        for key, label in (
            ("general", "Загальні"), ("models", "ШІ та моделі"),
            ("audio", "Аудіо та розпізнавання"), ("appearance", "Інтерфейс і теми"),
            ("performance", "Продуктивність"), ("keys", "Гарячі клавіші"),
            ("diagnostics", "Діагностика"),
        ):
            self.settings_navigation.addItem(label)
            scroll = QScrollArea()
            scroll.setObjectName("SettingsScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            content = QWidget()
            scroll.setWidget(content)
            layout = QVBoxLayout(content)
            layout.setContentsMargins(7, 8, 7, 8)
            layout.setSpacing(10)
            groups[key] = layout
            pages.addWidget(scroll)
        self.settings_navigation.currentRowChanged.connect(pages.setCurrentIndex)
        self.settings_navigation.setCurrentRow(0)
        layout = groups["models"]

        self.lbl_set_gemini = QLabel("GEMINI API KEY:")
        layout.addWidget(self.lbl_set_gemini)
        
        self.input_gemini_key = QLineEdit()
        self.input_gemini_key.setFixedHeight(36)
        self.input_gemini_key.setEchoMode(QLineEdit.Password)
        self.input_gemini_key.setText(self.config_mgr.get("gemini_api_key", ""))
        layout.addWidget(self.input_gemini_key)
        
        self.lbl_set_hint = QLabel("Введені ключі діють до закриття програми.")
        self.lbl_set_hint.setWordWrap(True)
        layout.addWidget(self.lbl_set_hint)

        self.save_history = QCheckBox("Зберігати питання та відповіді локально")
        self.save_history.setChecked(self.config_mgr.get("save_answer_history", False))
        groups["general"].addWidget(self.save_history)
        auto_send = QCheckBox("Автоматично надсилати голосові питання")
        auto_send.setChecked(self.auto_send.isChecked())
        auto_send.toggled.connect(self.auto_send.setChecked)
        self.auto_send.toggled.connect(auto_send.setChecked)
        groups["general"].addWidget(auto_send)

        self.lbl_stt_plan = QLabel("Мова: Gemini Live → локальний Whisper при збої")
        self.lbl_stt_plan.setWordWrap(True)
        groups["audio"].addWidget(self.lbl_stt_plan)

        self.lbl_set_model = QLabel("МОДЕЛЬ AI:")
        layout.addWidget(self.lbl_set_model)
        
        model_controls = QHBoxLayout()
        model_controls.addWidget(QLabel("Google Gemini"))
        self.combo_gemini = MinimalComboBox()
        self.combo_gemini.addItems(["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.1-flash-lite"])
        gemini = self.config_mgr.get("gemini_model")
        if self.combo_gemini.findText(gemini) < 0:
            self.combo_gemini.addItem(gemini)
        self.combo_gemini.setCurrentText(gemini)
        model_controls.addWidget(self.combo_gemini, 1)
        layout.addLayout(model_controls)
        model_controls = QHBoxLayout()
        layout = groups["audio"]
        model_controls.addWidget(QLabel("Whisper"))
        self.combo_whisper = MinimalComboBox()
        self.combo_whisper.addItem("Точніше (small)", "small")
        self.combo_whisper.addItem("Швидше (base)", "base")
        whisper = self.config_mgr.get("whisper_model_size")
        if self.combo_whisper.findData(whisper) < 0:
            self.combo_whisper.addItem("Налаштована модель", whisper)
        self.combo_whisper.setCurrentIndex(self.combo_whisper.findData(whisper))
        model_controls.addWidget(self.combo_whisper, 1)
        layout.addLayout(model_controls)
        self.combo_stt_language = MinimalComboBox()
        for label, code in (("Українська · Gemini Live / Whisper", "uk"),
                            ("Русский · Whisper", "ru"), ("English · Whisper", "en"),
                            ("Auto · Whisper", "auto")):
            self.combo_stt_language.addItem(label, code)
        self.combo_stt_language.setCurrentIndex(max(
            0, self.combo_stt_language.findData(self.config_mgr.get("whisper_language", "uk"))
        ))
        layout.addWidget(self.combo_stt_language)

        self.combo_speech_pace = MinimalComboBox()
        self.combo_speech_pace.addItem("Темп мовлення: Auto", "auto")
        self.combo_speech_pace.addItem("Темп мовлення: звичайний", "normal")
        self.combo_speech_pace.addItem("Темп мовлення: повільний / з паузами", "slow")
        self.combo_speech_pace.addItem("Повне питання: довгі паузи", "complete")
        self.combo_speech_pace.setCurrentIndex(max(
            0, self.combo_speech_pace.findData(self.config_mgr.get("speech_pace_mode", "complete"))
        ))
        layout.addWidget(self.combo_speech_pace)

        layout = groups["performance"]
        self.lbl_ui_performance = QLabel("ШВИДКОДІЯ ІНТЕРФЕЙСУ:")
        layout.addWidget(self.lbl_ui_performance)
        self.combo_ui_performance = MinimalComboBox()
        for name in ("auto", *PROFILES):
            self.combo_ui_performance.addItem(name.upper(), name)
        saved = self.config_mgr.get("ui_performance_mode", "auto")
        self.combo_ui_performance.setCurrentIndex(max(
            0, self.combo_ui_performance.findData(LEGACY.get(saved, saved))
        ))
        self.combo_ui_performance.currentIndexChanged.connect(
            lambda: self.save_appearance("ui_performance_mode", self.combo_ui_performance.currentData())
        )
        layout.addWidget(self.combo_ui_performance)
        self.performance_note = QLabel()
        self.performance_note.setWordWrap(True)
        layout.addWidget(self.performance_note)
        self.init_appearance_controls(groups["appearance"])

        layout = groups["audio"]

        self.lbl_set_source = QLabel("ДЖЕРЕЛО АУДІО:")
        layout.addWidget(self.lbl_set_source)
        
        self.combo_audio_source = MinimalComboBox()
        self.combo_audio_source.addItems(["Системний звук (Loopback)", "Мікрофон"])
        current_mode = self.config_mgr.get("audio_mode", "loopback")
        self.combo_audio_source.setCurrentIndex(0 if current_mode == "loopback" else 1)
        layout.addWidget(self.combo_audio_source)

        self.lbl_set_device = QLabel("АУДІОПРИСТРІЙ:")
        layout.addWidget(self.lbl_set_device)
        
        self.combo_device_select = MinimalComboBox()
        layout.addWidget(self.combo_device_select)
        self.combo_audio_source.currentIndexChanged.connect(self.refresh_audio_devices)

        self.btn_save_cfg = QPushButton("ЗБЕРЕГТИ НАЛАШТУВАННЯ")
        self.btn_save_cfg.setFixedHeight(38)
        self.btn_save_cfg.setMinimumWidth(104)
        self.btn_save_cfg.setObjectName("BtnPrimary")
        self.btn_save_cfg.clicked.connect(self.save_settings)
        actions = QHBoxLayout()
        self.settings_feedback = QLabel()
        self.settings_feedback.setWordWrap(True)
        actions.addWidget(self.settings_feedback, 1)
        actions.addWidget(self.btn_save_cfg)
        back = QPushButton("Повернутися")
        back.setMinimumSize(104, 38)
        back.clicked.connect(window.close)
        actions.addWidget(back)
        root.addLayout(actions)
        layout = groups["diagnostics"]
        self.transcript_diagnostics = QCheckBox("Виводити транскрипції в консоль")
        layout.addWidget(self.transcript_diagnostics)
        notice = QLabel("Може містити приватний текст. Діє до закриття програми. "
                        "Текст виводиться локально у stderr.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        for shortcut in ("Ctrl+Alt+R · слухати / зупинити", "Ctrl+Shift+F12 · повернути Dock"):
            key_label = QLabel(shortcut)
            key_label.setWordWrap(True)
            groups["keys"].addWidget(key_label)
        self.btn_open_timing = QPushButton("ВІКНО ЗАМІРІВ")
        self.btn_open_timing.setObjectName("BtnSecondary")
        self.btn_open_timing.setFixedHeight(38)
        self.btn_open_timing.clicked.connect(self.developer_timing_signal.emit)
        layout.addWidget(self.btn_open_timing)
        for group in groups.values():
            group.addStretch()

    def init_appearance_controls(self, layout):
        def choice(label, options, key, default):
            layout.addWidget(QLabel(label))
            combo = MinimalComboBox()
            for title, value in options:
                combo.addItem(title, value)
            combo.setCurrentIndex(max(0, combo.findData(self.config_mgr.get(key, default))))
            combo.currentIndexChanged.connect(
                lambda: self.save_appearance(key, combo.currentData())
            )
            layout.addWidget(combo)

        choice("Тема", [(theme[0], key) for key, theme in THEMES.items()], "theme", "midnight")
        accent = QPushButton("Вибрати колір Custom Accent")
        accent.clicked.connect(self.pick_accent)
        layout.addWidget(accent)
        layout.addWidget(QLabel("Щільність тонування скла (%)"))
        opacity = QSpinBox()
        opacity.setRange(75, 100)
        opacity.setValue(round(100 * self.config_mgr.get("window_opacity", .98)))
        opacity.editingFinished.connect(
            lambda: self.save_appearance("window_opacity", opacity.value() / 100)
        )
        layout.addWidget(opacity)
        for label, key in (("Системне розмиття (якщо сумісне)", "system_blur"),
                           ("Зменшити рух", "reduce_motion")):
            checkbox = QCheckBox(label)
            checkbox.setChecked(self.config_mgr.get(key))
            checkbox.toggled.connect(lambda checked, name=key: self.save_appearance(name, checked))
            layout.addWidget(checkbox)
        choice("Інтенсивність анімацій", [("Повна", "full"), ("Помірна", "subtle"),
                                          ("Вимкнено", "off")], "animation_intensity", "full")
        choice("Закріплення", [("Top Left", "left"), ("Top Center", "center"),
                               ("Top Right", "right")], "dock_anchor", "center")
        choice("Монітор", [("Основний", "")] + [
            (screen.name(), screen.name()) for screen in QGuiApplication.screens()
        ], "dock_screen", "")
        recovery = QLabel("Відновити вікно · Ctrl+Shift+F12")
        recovery.setWordWrap(True)
        layout.addWidget(recovery)

    def pick_accent(self):
        current = self.config_mgr.get("accent")
        color = QColorDialog.getColor(QColor(current), self.settings_window, "Custom Accent")
        if color.isValid():
            self.save_appearance("accent", color.name())

    def save_appearance(self, key, value):
        try:
            self.config_mgr.set(key, value)
        except OSError:
            self.settings_feedback.setText("Помилка збереження / Save error")
            return
        self.settings_feedback.clear()
        if key in {"dock_anchor", "dock_screen"}:
            if self.window_mode.docked:
                self.window_mode.apply()
        elif key == "ui_performance_mode":
            self.apply_performance_mode(value)
        else:
            self.apply_aurora_theme()
            self.apply_performance_mode(self.config_mgr.get("ui_performance_mode"))

    def init_about_tab(self):
        outer = QVBoxLayout(self.tab_about)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setSpacing(11)
        title = QLabel("EXAM AI")
        title.setObjectName("BrandTitle")
        layout.addWidget(title)
        layout.addWidget(QLabel("Real-Time AI Desktop Copilot"))
        description = QLabel(
            "EXAM AI — інтелектуальний настільний асистент для роботи з голосовими "
            "запитаннями, програмним кодом, документами та зображеннями. Поєднує "
            "розпізнавання мовлення, пошук за змістом і швидку генерацію відповідей "
            "у зручному інтерфейсі Windows."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        for heading, text in (
            ("Можливості", "Голосові питання, потокові відповіді FAST / SMART, Compact Dock "
                           "з Live Cards, пошук у конспектах, аналіз виділеної області екрана."),
            ("Розробники", "nega71ve · shibuya"),
            ("Технології", "Python · PySide6 · WASAPI · Google Gemini API · faster-whisper · SQLite · E5 / BM25"),
            ("Версія", VERSION),
            ("GitHub", REPOSITORY),
            ("Ліцензії", "PySide6 Essentials — LGPL-3.0-only / GPL-2.0-only / GPL-3.0-only. faster-whisper — MIT."),
            ("Подяки", "Дякуємо спільнотам відкритого ПЗ за інструменти, на яких працює EXAM AI."),
        ):
            card = QFrame()
            card.setObjectName("LiveCard")
            section = QVBoxLayout(card)
            label = QLabel(heading)
            label.setObjectName("HeaderLabel")
            section.addWidget(label)
            detail = QLabel(text)
            detail.setWordWrap(True)
            detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            section.addWidget(detail)
            layout.addWidget(card)
        buttons = QHBoxLayout()
        repository = QPushButton("Відкрити репозиторій")
        repository.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPOSITORY)))
        version = QPushButton("Скопіювати версію")
        version.clicked.connect(lambda: QApplication.clipboard().setText(VERSION))
        for button in (repository, version):
            button.setMinimumHeight(36)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        layout.addStretch()

    def watch_screen_geometry(self, screen):
        screen.availableGeometryChanged.connect(self.on_screen_geometry_changed)

    def on_screen_geometry_changed(self, _area):
        if self.window_mode.docked:
            self.window_mode.apply()

    def on_screen_removed(self, _screen):
        if self.window_mode.docked:
            QTimer.singleShot(0, self.window_mode.apply)

    def init_docs_tab(self):
        layout = QVBoxLayout(self.tab_docs)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        self.btn_upload = QPushButton("+ ДОДАТИ КОНСПЕКТ")
        self.btn_upload.setFixedHeight(38)
        self.btn_upload.setObjectName("BtnSecondary")
        self.btn_upload.clicked.connect(self.upload_file)
        layout.addWidget(self.btn_upload)

        tools_layout = QHBoxLayout()
        tools_layout.setSpacing(8)
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("Введи термін для пошуку...")
        self.input_search.setFixedHeight(34)
        self.input_search.setMinimumWidth(0)
        
        self.btn_search = QPushButton("ЗНАЙТИ")
        self.btn_search.setFixedHeight(34)
        self.btn_search.setFixedWidth(88)
        self.btn_search.setObjectName("BtnPrimary")
        self.btn_search.clicked.connect(lambda: self.search_keyword_signal.emit(self.input_search.text()))

        self.btn_summary = QPushButton("АВТОКОНСПЕКТ")
        self.btn_summary.setFixedHeight(34)
        self.btn_summary.setObjectName("BtnSecondary")
        self.btn_summary.clicked.connect(lambda: self.auto_summary_signal.emit(self.list_docs.currentItem().text() if self.list_docs.currentItem() else ""))

        tools_layout.addWidget(self.input_search, stretch=1)
        tools_layout.addWidget(self.btn_search)
        layout.addLayout(tools_layout)

        self.list_docs = QListWidget()
        self.list_docs.setMaximumHeight(80)
        layout.addWidget(self.list_docs)
        layout.addWidget(self.btn_summary)

        self.btn_delete_doc = QPushButton("✕ ВИДАЛИТИ ВИБРАНИЙ ФАЙЛ")
        self.btn_delete_doc.setFixedHeight(30)
        self.btn_delete_doc.setObjectName("BtnDanger")
        self.btn_delete_doc.clicked.connect(self.delete_selected_document)
        layout.addWidget(self.btn_delete_doc)

        self.txt_docs_result = ResponseView()
        self.txt_docs_result.setPlaceholderText("Тут з'являться результати пошуку або автоконспект...")
        layout.addWidget(self.txt_docs_result)

    def refresh_documents_list(self):
        selected = self.list_docs.currentItem()
        filename = selected.text() if selected else None
        try:
            sources = self.db_mgr.get_documents_list()
        except sqlite3.Error:
            self.txt_docs_result.setPlainText("Не вдалося оновити список документів.")
            return
        self.list_docs.clear()
        self.list_docs.addItems(sources)
        if filename in sources:
            self.list_docs.setCurrentRow(sources.index(filename))

    def delete_selected_document(self):
        item = self.list_docs.currentItem()
        if item:
            self.file_deleted_signal.emit(item.text())

    def refresh_audio_devices(self):
        self.combo_device_select.clear()
        is_loopback = self.combo_audio_source.currentIndex() == 0
        default_label = ("Пристрій відтворення Windows за замовчуванням"
                         if is_loopback else "Мікрофон за замовчуванням")
        self.combo_device_select.addItem(default_label, -1)
        try:
            devices = AudioCapturer.get_available_devices()
        except Exception:
            self.lbl_status.setText("Не вдалося отримати список аудіопристроїв.")
            return
        for dev in devices:
            if bool(dev["loopback"]) == is_loopback:
                self.combo_device_select.addItem(dev["name"], dev["index"])
        mode = "loopback" if is_loopback else "mic"
        if mode == self.config_mgr.get("audio_mode"):
            index = self.combo_device_select.findData(self.config_mgr.get("audio_device_index"))
            self.combo_device_select.setCurrentIndex(max(0, index))

    def on_ghost_toggled(self, checked):
        if self.btn_ghost.locked:
            return
        self.ghost_toggled_signal.emit(checked)

    def on_ghost_lock_changed(self, locked):
        self.btn_ghost.set_locked(locked)
        self.show_toast("Ghost Mode: замок закрито" if locked else "Ghost Mode: замок відкрито")
        try:
            self.config_mgr.set("ghost_controls_locked", bool(locked))
        except OSError:
            self.lbl_status.setText("Не вдалося зберегти стан замка.")

    def set_privacy_button(self, enabled, requested=None, error=""):
        self._privacy_verified = bool(enabled)
        self._privacy_requested = bool(enabled if requested is None else requested)
        self._privacy_error = error
        self.btn_ghost.set_feedback(enabled, self._privacy_requested, error)

    def motion_ms(self, base):
        intensity = self.config_mgr.get("animation_intensity")
        if self.config_mgr.get("reduce_motion") or self.profile == "eco" or intensity == "off":
            return 0
        return round(base * (0.7 if intensity == "subtle" or self.profile == "balanced" else 1))

    def on_performance_sample(self, profile, samples):
        if profile and self.config_mgr.get("ui_performance_mode") == "auto":
            self.apply_performance_mode("auto")
        self.performance_note.setText(
            f"{self.profile.upper()} · CPU {samples['process_cpu']:.0f}% · "
            f"RAM {samples['rss_mib']:.0f} MiB · GUI {samples['lag_ms']:.0f} ms · "
            f"{samples.get('available_gib', self.hardware.available_gib):.1f} GiB вільно"
        )

    def apply_performance_mode(self, mode):
        selected = LEGACY.get(mode, mode)
        self.profile = selected if selected in PROFILES else self.performance_policy.profile
        if selected == "auto" and self.config_mgr.get("resolved_profile") != self.profile:
            try:
                self.config_mgr.set("resolved_profile", self.profile)
            except OSError:
                pass
        motion = self.motion_ms(220)
        self.audio_visualizer.set_performance_mode(
            "visual" if self.profile == "premium" and motion else "balanced" if motion else "performance"
        )
        self.btn_toggle_listen.set_animation_enabled(bool(motion))
        self.btn_ghost.set_animation_enabled(bool(motion))
        self.sidebar.setFixedWidth(132 if self.profile == "eco" else 160)
        self.fade_anim.stop()
        self.fade_anim.setDuration(motion)
        self.fade_effect.setOpacity(1.0)
        self.fade_effect.setEnabled(False)
        if hasattr(self, "toast_anim_in") and not motion:
            self.toast_anim_in.stop()
            self.toast_anim_out.stop()
            self.toast_opacity.setOpacity(1.0)
        if self.isVisible():
            self.apply_backdrop()

    def apply_backdrop(self):
        enabled = self.config_mgr.get("system_blur") and self.profile != "eco"
        self.backdrop_active = set_windows_backdrop(self, enabled)
        # The native window stays opaque for legible text and display affinity.
        # The glass setting controls the tint of the painted shell.
        self.setWindowOpacity(1.0)

    def showEvent(self, event):
        super().showEvent(event)
        self.apply_backdrop()

    def apply_aurora_theme(self):
        template = """
        #SettingsWindow, #ExamAIWindow { background-color: #0a173b; border: none; }
        #CentralContainer {
            background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #192f67, stop:0.48 #102454, stop:1 #0b1b42);
            border: 1px solid #526fac; border-radius: 24px;
        }
        QWidget { color: #e7efff; font-family: 'Segoe UI'; font-size: 13px; }
        #BrandTitle { color: #f7f9ff; font-size: 18px; font-weight: 700; }
        #BrandIcon {
            color: white; font-size: 23px; font-weight: 700;
            background-color: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 #5d70e8, stop:1 #9b62ea);
            border: 1px solid #a1acff; border-radius: 13px;
        }
        #LiveCard { background-color: #203a72; border: 1px solid #526fa8; border-radius: 11px; }
        #LiveCardText { background-color: #1b3268; color: #f0f5ff; border: none; border-radius: 6px; }
        #LiveCard QPushButton { min-height: 20px; padding: 0 6px; border-radius: 7px; }
        #CompactHeader QPushButton { padding: 0 9px; }
        #LangContainer, #HeaderPill, #HeaderMode {
            background-color: #263a70; border: 1px solid #526b9e;
            border-radius: 15px; color: #e5ebff;
        }
        #HeaderPill { min-width: 110px; padding: 0 9px; font-weight: 700; }
        #HeaderMode { min-width: 105px; padding: 4px 10px; font-weight: 700; }
        #LangBtn, #LangBtnActive {
            border: none; border-radius: 10px; padding: 3px 8px;
            font-size: 11px; font-weight: 700;
        }
        #LangBtn { color: #b9c7eb; background: transparent; }
        #LangBtnActive {
            color: white;
            background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #6079e6, stop:1 #8a5de4);
        }
        #SideBar {
            background-color: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 #1d356b, stop:1 #142854);
            border: 1px solid #39558f; border-radius: 18px;
        }
        #NavButton, #NavButtonActive {
            text-align: left; padding-left: 15px; border-radius: 13px;
            font-size: 13px; font-weight: 600;
        }
        #NavButton { color: #b9c8eb; background: transparent; border: none; }
        #NavButton:hover { background-color: #2b467f; color: white; }
        #NavButtonActive {
            color: white; border: 1px solid #668ee2;
            background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #3c5cb6, stop:1 #294783);
        }
        #StatusLabel {
            color: #a8f1d0; font-size: 11px; font-weight: 600;
            background-color: #1b315f; border-radius: 8px; padding: 4px 6px;
        }
        #SettingsScroll { background: transparent; border: none; }
        QScrollArea > QWidget > QWidget { background: transparent; }
        QScrollBar:vertical {
            background: #172b56; width: 9px; margin: 2px 0;
            border: none; border-radius: 4px;
        }
        QScrollBar::handle:vertical {
            background: #607bb7; min-height: 30px; border-radius: 4px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0; border: none;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: transparent;
        }
        QTabWidget::pane { border: none; background: transparent; }
        #QuestionEditor, #AnswerView, #HistoryList, #ComposerInput,
        QTextEdit, QLineEdit, QListWidget {
            color: #f0f5ff; background-color: #203a72;
            border: 1px solid #526fa8; border-radius: 17px;
            padding: 12px; selection-background-color: #5478d2;
        }
        #QuestionEditor:focus, #ComposerInput:focus, QLineEdit:focus {
            border: 1px solid #96b7ff;
        }
        #QuestionEditor { font-size: 14px; }
        #AnswerView { background-color: #1b3268; font-size: 14px; }
        #ComposerInput { min-height: 20px; font-size: 13px; }
        QComboBox {
            background-color: #263e73; color: #f1f5ff;
            border: 1px solid #5a73a6; border-radius: 12px;
            padding: 5px 30px 5px 10px; min-height: 24px;
        }
        QComboBox::drop-down { border: none; width: 30px; }
        QComboBox::down-arrow { image: none; width: 0; height: 0; }
        QComboBox QAbstractItemView {
            background-color: #203766; color: #f1f5ff;
            selection-background-color: #4b69b8;
        }
        QPushButton {
            color: #e8efff; background-color: #263e73;
            border: 1px solid #5b75a8; border-radius: 13px;
            font-weight: 700;
        }
        QPushButton:hover { background-color: #35518b; border-color: #8eacf0; }
        #BtnListenStart {
            color: white; font-size: 16px; border: none;
            border-radius: 27px;
            background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #2c8ff2, stop:0.45 #5378e8, stop:1 #a45add);
        }
        #BtnListenStart:hover {
            background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #4aa7ff, stop:0.5 #6e8df5, stop:1 #b674ee);
        }
        #BtnListenStop {
            color: #ffd6e0; font-size: 16px; border-radius: 27px;
            background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #9b3f7a, stop:1 #6747a1); border: none;
        }
        #BtnPrimary {
            color: white; font-size: 15px; border-radius: 27px;
            background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #2e5b9f, stop:1 #425b9b); border: none;
        }
        #BtnSecondary { background-color: #2a447c; }
        #BtnDanger { color: #ffb8c9; background-color: #4c2949; }
        #SmallBtn {
            color: #b8d2ff; background: transparent; border: none;
            font-size: 11px; text-decoration: underline;
        }
        #HeaderLabel { color: #b7c7eb; font-size: 11px; font-weight: 700; }
        #HeaderLabelActive { color: #d8c7ff; font-size: 12px; font-weight: 700; }
        #ComposerSend {
            color: #e9f5ff; background-color: #4d72d4;
            border: 1px solid #92b2f1; border-radius: 20px; font-size: 18px;
        }
        #WindowBtn, #CloseBtn {
            color: #b6c7ec; background: transparent; border: none;
        }
        #CloseBtn:hover { color: #ffc0d3; }
        #ToastLabel {
            color: #d5ffe9; background-color: #21486d;
            border: 1px solid #67a7cf; border-radius: 12px;
            padding: 6px 12px; font-weight: 700;
        }
        QCheckBox { color: #c9d8f7; spacing: 8px; }
        QCheckBox::indicator {
            width: 15px; height: 15px; border-radius: 5px;
            border: 1px solid #8ba8e2; background-color: #213767;
        }
        QCheckBox::indicator:checked { background-color: #7089ed; }
        """
        theme = self.config_mgr.get("theme")
        tint = max(.75, min(1.0, self.config_mgr.get("window_opacity", .98)))
        template = stylesheet(template, theme, self.config_mgr.get("accent"))
        base = THEMES.get(theme, THEMES["midnight"])[1]
        glass = blend(base, "#8594b0", (1 - tint) * .35)
        self.setStyleSheet(template.replace(
            "#SettingsWindow, #ExamAIWindow { background-color: " + base,
            "#SettingsWindow, #ExamAIWindow { background-color: " + glass
        ).replace("stop:0 " + blend(base, THEMES.get(theme, THEMES["midnight"])[4], .055),
                  "stop:0 " + glass, 1))
        self.settings_window.setStyleSheet(self.styleSheet())
        foreground = "#192337" if theme == "frosted_light" else "#f4f4f4"
        for view in (self.txt_answer, self.txt_docs_result):
            view.set_formula_color(foreground)
        self.txt_answer.setFont(QFont("Segoe UI", 11))
        self.txt_question.setFont(QFont("Segoe UI", 11))

    def set_compact_status(self, text):
        self.compact_status.setToolTip(text)
        self.refresh_compact_status()

    def refresh_compact_status(self):
        label = self.compact_status
        label.setText(label.fontMetrics().elidedText(label.toolTip(), Qt.TextElideMode.ElideRight,
                                                      max(0, label.contentsRect().width() - 12)))

    def eventFilter(self, watched, event):
        if watched is getattr(self, "compact_status", None):
            if event.type() == QEvent.Type.Resize:
                self.refresh_compact_status()
            elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
                self.live_cards.reopen()
        if event.type() == QEvent.Type.EnabledChange and hasattr(self, "btn_select_screen"):
            if watched is self.btn_toggle_listen:
                self.compact_listen.setEnabled(watched.isEnabled())
            elif watched is self.btn_select_screen:
                self.compact_screen.setEnabled(watched.isEnabled())
        return super().eventFilter(watched, event)

    def on_toggle_listen(self):
        if self.btn_toggle_listen.isEnabled():
            is_listening = self.btn_toggle_listen.text() in ["СТОП", "STOP"]
            signal = (
                self.start_listening_signal
                if not is_listening
                else self.stop_listening_signal
            )
            signal.emit()

    def set_listening(self, enabled):
        stop_txt = "СТОП" if self.tr.current_lang == "UA" else "STOP"
        listen_txt = "СЛУХАТИ" if self.tr.current_lang == "UA" else "LISTEN"
        
        self.btn_toggle_listen.setText(stop_txt if enabled else listen_txt)
        self.compact_listen.setText(self.btn_toggle_listen.text())
        self.btn_toggle_listen.set_recording(enabled)
        self.audio_visualizer.set_active(enabled)
        self.btn_toggle_listen.setObjectName("BtnListenStop" if enabled else "BtnListenStart")
        self.btn_toggle_listen.style().unpolish(self.btn_toggle_listen)
        self.btn_toggle_listen.style().polish(self.btn_toggle_listen)
        self.btn_toggle_listen.setEnabled(True)

    def clear_both(self):
        self.txt_question.clear()
        self.txt_answer.clear()
        self.clear_all_signal.emit()

    def set_history_position(self, position, total, *, busy=False):
        self.live_cards.history_total = total
        self.live_cards.previous.setEnabled(not busy and total > 0 and position != 1)
        self.live_cards.latest.setEnabled(not busy and total > 0 and position != total)
        self.lbl_history_position.setText(f"{position}/{total}")
        self.btn_history_prev.setEnabled(not busy and (position > 1 or (position == 0 and total > 0)))
        self.btn_history_next.setEnabled(not busy and 0 < position < total)

    def closeEvent(self, event):
        event.ignore()
        try:
            self.window_mode.remember()
        except OSError:
            pass
        self.close_requested.emit()

    def upload_file(self):
        title = "Виберіть конспект" if self.tr.current_lang == "UA" else "Select note"
        file_path, _ = QFileDialog.getOpenFileName(
            self, title, "", "Documents (*.pdf *.docx *.txt)",
        )
        if file_path:
            self.file_uploaded_signal.emit(file_path)

    def save_auto_send(self, enabled):
        try:
            self.config_mgr.set("auto_send", enabled)
        except OSError:
            pass

    def save_answer_mode(self, _index):
        try:
            self.config_mgr.set('answer_mode', self.answer_mode.currentData())
        except OSError:
            self.lbl_toast.setText('Помилка збереження / Save error')

    def save_settings(self):
        selected_mode = "loopback" if self.combo_audio_source.currentIndex() == 0 else "mic"
        try:
            self.config_mgr.save_config({
                "gemini_api_key": self.input_gemini_key.text().strip(),
                "gemini_model": self.combo_gemini.currentText(),
                "save_answer_history": self.save_history.isChecked(),
                "whisper_model_size": self.combo_whisper.currentData(),
                "whisper_language": self.combo_stt_language.currentData(),
                "speech_pace_mode": self.combo_speech_pace.currentData(),
                "ui_performance_mode": self.combo_ui_performance.currentData(),
                "audio_mode": selected_mode,
                "audio_device_index": self.combo_device_select.currentData(),
                "language": self.tr.current_lang,
            })
        except OSError:
            self.settings_feedback.setText("Помилка збереження / Save error")
        else:
            self.apply_performance_mode(self.combo_ui_performance.currentData())
            self.settings_feedback.setText(self.tr.get("toast_saved"))

    @Slot(float)
    def update_audio_indicator(self, rms_val: float):
        self.audio_visualizer.set_rms(rms_val)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.window_mode.docked:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.window_mode.docked and event.buttons() & Qt.LeftButton and hasattr(self, "_drag_pos"):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if hasattr(self, "_drag_pos"):
            del self._drag_pos
        super().mouseReleaseEvent(event)
