import sqlite3
import math
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QComboBox, QTabWidget, QFileDialog, QProgressBar, QLineEdit,
    QListWidget, QGraphicsOpacityEffect, QCheckBox, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, Slot, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont, QGuiApplication, QPainter, QColor, QPen
from audio_capturer import AudioCapturer
from response_view import ResponseView
from translator import Translator
from rounded_window import configure_rounded_window
from ghost_controls import GhostToggle, GhostLockButton


import math

class AudioVisualizer(QWidget):
    """Плавный, вайбовый и цельный визуализатор звука (мягкая волна)"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(16)
        self.target_rms = 0.0
        self.current_rms = 0.0
        self._phase = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(20)

    def set_rms(self, val):
        self.target_rms = val

    def update_animation(self):
        self.current_rms += (self.target_rms - self.current_rms) * 0.15
        self._phase += 0.08
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        width = self.width()
        height = self.height()
        mid_y = height / 2.0
        
        alpha = int(70 + min(1.0, self.current_rms * 12) * 185)
        pen_color = QColor(255, 255, 255, alpha)
        pen = QPen(pen_color)
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        from PySide6.QtGui import QPainterPath
        path = QPainterPath()
        
        points = 30
        step = width / (points - 1)
        amplitude = height * 0.45 * min(1.0, self.current_rms * 8)
        
        for i in range(points):
            x = i * step
            sin_factor = math.sin(self._phase + i * 0.3) * math.cos(self._phase * 0.5 - i * 0.1)
            y = mid_y + sin_factor * amplitude
            
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
                
        painter.drawPath(path)


class MainWindow(QMainWindow):
    close_requested = Signal()
    question_submitted = Signal(str)
    clear_all_signal = Signal()
    start_listening_signal = Signal()
    stop_listening_signal = Signal()
    file_uploaded_signal = Signal(str)
    file_deleted_signal = Signal(str)
    ghost_toggled_signal = Signal(bool)
    search_keyword_signal = Signal(str)
    auto_summary_signal = Signal(str)

    def __init__(self, config_mgr, db_mgr):
        super().__init__()
        self.config_mgr = config_mgr
        self.db_mgr = db_mgr
        
        saved_lang = self.config_mgr.get("language", "UA")
        self.tr = Translator(saved_lang)

        self.setWindowTitle("Exam AI")
        self.setObjectName("ExamAIWindow")
        self.resize(520, 700)

        self.update_window_flags()
        configure_rounded_window(
            self, radius=14, opacity=self.config_mgr.get("window_opacity", 0.95),
        )

        self.init_ui()
        self.apply_monochrome_theme()
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
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(10)

        # Header
        top_bar = QHBoxLayout()
        top_bar.setSpacing(4)

        self.lbl_title = QLabel("EXAM AI")
        self.lbl_title.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.lbl_title.setStyleSheet("color: #ffffff; letter-spacing: 2px;")

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

        top_bar.addWidget(self.lbl_title)
        top_bar.addSpacing(12)
        top_bar.addWidget(lang_container)
        top_bar.addStretch()
        top_bar.addWidget(self.btn_ghost)
        top_bar.addWidget(self.btn_ghost_lock)
        top_bar.addSpacing(6)
        top_bar.addWidget(btn_min)
        top_bar.addWidget(btn_close)
        main_layout.addLayout(top_bar)

        # Tabs
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self.fade_effect = QGraphicsOpacityEffect(self.tabs)
        self.tabs.setGraphicsEffect(self.fade_effect)
        self.fade_anim = QPropertyAnimation(self.fade_effect, b"opacity")
        self.fade_anim.setDuration(220)
        self.tabs.currentChanged.connect(self.animate_tab_change)

        self.tab_live = QWidget()
        self.init_live_tab()
        self.tabs.addTab(self.tab_live, "AI")

        self.tab_settings = QWidget()
        self.init_settings_tab()
        self.tabs.addTab(self.tab_settings, "Налаштування")

        self.tab_docs = QWidget()
        self.init_docs_tab()
        self.tabs.addTab(self.tab_docs, "Конспекти")

        # Footer
        status_bar = QHBoxLayout()
        self.lbl_status = QLabel("Готовий")
        self.lbl_status.setStyleSheet("color: #777777; font-weight: 700; font-size: 11px;")
        
        self.audio_visualizer = AudioVisualizer()
        self.audio_visualizer.setFixedWidth(80)

        status_bar.addWidget(self.lbl_status)
        status_bar.addStretch()
        status_bar.addWidget(self.audio_visualizer)
        main_layout.addLayout(status_bar)

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
        self.toast_anim_in.setStartValue(0.0)
        self.toast_anim_in.setEndValue(1.0)
        self.toast_anim_in.start()

        self._toast_timer.start(1800)

    def hide_toast(self):
        self.toast_anim_in.stop()
        self.toast_anim_out.setStartValue(self.toast_opacity.opacity())
        self.toast_anim_out.start()

    def retranslate_ui(self):
        self.tabs.setTabText(0, self.tr.get("tab_ai"))
        self.tabs.setTabText(1, self.tr.get("tab_settings"))
        self.tabs.setTabText(2, self.tr.get("tab_docs"))
        
        is_listening = self.btn_toggle_listen.text() in ["СТОП", "STOP"]
        self.btn_toggle_listen.setText(self.tr.get("btn_stop") if is_listening else self.tr.get("btn_listen"))
        self.btn_copy_ans.setText(self.tr.get("btn_copy"))
        self.lbl_q_title.setText(self.tr.get("lbl_question"))
        self.btn_clear_all.setText(self.tr.get("btn_clear_all"))
        self.auto_send.setText(self.tr.get("auto_send"))
        self.btn_send_q.setText(self.tr.get("btn_send"))
        self.lbl_a_title.setText(self.tr.get("lbl_answer"))
        self.lbl_status.setText(self.tr.get("status_ready"))
        
        self.lbl_set_gemini.setText(self.tr.get("set_gemini"))
        self.lbl_set_hint.setText(self.tr.get("set_keys_hint"))
        self.lbl_stt_plan.setText(self.tr.get("set_stt_plan"))
        self.lbl_set_model.setText(self.tr.get("set_model"))
        self.lbl_set_source.setText(self.tr.get("set_audio_source"))
        self.lbl_set_device.setText(self.tr.get("set_audio_device"))
        self.btn_save_cfg.setText(self.tr.get("btn_save_settings"))
        
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
        self.fade_anim.stop()
        self.fade_anim.setStartValue(0.3)
        self.fade_anim.setEndValue(1.0)
        self.fade_anim.start()

    def init_live_tab(self):
        layout = QVBoxLayout(self.tab_live)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        ctrl_box = QHBoxLayout()
        self.btn_toggle_listen = QPushButton("СЛУХАТИ")
        self.btn_toggle_listen.setFixedHeight(42)
        self.btn_toggle_listen.setObjectName("BtnListenStart")
        self.btn_toggle_listen.clicked.connect(self.on_toggle_listen)

        self.btn_copy_ans = QPushButton("КОПІЮВАТИ")
        self.btn_copy_ans.setFixedHeight(42)
        self.btn_copy_ans.setObjectName("BtnSecondary")
        self.btn_copy_ans.clicked.connect(self.copy_answer)

        ctrl_box.addWidget(self.btn_toggle_listen, stretch=3)
        ctrl_box.addWidget(self.btn_copy_ans, stretch=2)
        layout.addLayout(ctrl_box)

        q_header = QHBoxLayout()
        self.lbl_q_title = QLabel("ПИТАННЯ:")
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
        self.txt_question.setMaximumHeight(80)
        layout.addWidget(self.txt_question)

        question_controls = QHBoxLayout()
        self.auto_send = QCheckBox("Автоматично надсилати з аудіо")
        self.auto_send.setChecked(self.config_mgr.get("auto_send", True))
        self.auto_send.toggled.connect(self.save_auto_send)
        question_controls.addWidget(self.auto_send)
        self.answer_mode = QComboBox()
        self.answer_mode.addItem("FAST", "fast")
        self.answer_mode.addItem("SMART", "smart")
        self.answer_mode.setCurrentIndex(1 if self.config_mgr.get("answer_mode") == "smart" else 0)
        self.answer_mode.currentIndexChanged.connect(self.save_answer_mode)
        question_controls.addWidget(self.answer_mode)
        question_controls.addStretch()
        
        self.btn_send_q = QPushButton("Надіслати питання")
        self.btn_send_q.setObjectName("SmallBtn")
        self.btn_send_q.clicked.connect(lambda: self.question_submitted.emit(self.txt_question.toPlainText()))
        question_controls.addWidget(self.btn_send_q)
        layout.addLayout(question_controls)

        a_header = QHBoxLayout()
        self.lbl_a_title = QLabel("ВІДПОВІДЬ AI:")
        self.lbl_a_title.setObjectName("HeaderLabelActive")
        a_header.addWidget(self.lbl_a_title)
        a_header.addStretch()
        layout.addLayout(a_header)

        self.txt_answer = ResponseView()
        layout.addWidget(self.txt_answer)

    def init_settings_tab(self):
        layout = QVBoxLayout(self.tab_settings)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        self.lbl_set_gemini = QLabel("GEMINI API KEY:")
        layout.addWidget(self.lbl_set_gemini)
        
        self.input_gemini_key = QLineEdit()
        self.input_gemini_key.setFixedHeight(36)
        self.input_gemini_key.setEchoMode(QLineEdit.Password)
        self.input_gemini_key.setText(self.config_mgr.get("gemini_api_key", ""))
        layout.addWidget(self.input_gemini_key)
        
        self.lbl_set_hint = QLabel("Введені ключі діють до закриття програми.")
        layout.addWidget(self.lbl_set_hint)

        self.save_history = QCheckBox("Зберігати питання та відповіді локально")
        self.save_history.setChecked(self.config_mgr.get("save_answer_history", False))
        layout.addWidget(self.save_history)

        self.lbl_stt_plan = QLabel("Мова: Gemini Live → локальний Whisper при збої")
        layout.addWidget(self.lbl_stt_plan)

        self.lbl_set_model = QLabel("МОДЕЛЬ AI:")
        layout.addWidget(self.lbl_set_model)
        
        model_controls = QHBoxLayout()
        model_controls.addWidget(QLabel("Gemini"))
        self.combo_gemini = QComboBox()
        self.combo_gemini.addItems(["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.1-flash-lite"])
        gemini = self.config_mgr.get("gemini_model")
        if self.combo_gemini.findText(gemini) < 0:
            self.combo_gemini.addItem(gemini)
        self.combo_gemini.setCurrentText(gemini)
        model_controls.addWidget(self.combo_gemini, 1)
        model_controls.addWidget(QLabel("Whisper"))
        self.combo_whisper = QComboBox()
        self.combo_whisper.addItem("Точніше (small)", "small")
        self.combo_whisper.addItem("Швидше (base)", "base")
        whisper = self.config_mgr.get("whisper_model_size")
        if self.combo_whisper.findData(whisper) < 0:
            self.combo_whisper.addItem("Налаштована модель", whisper)
        self.combo_whisper.setCurrentIndex(self.combo_whisper.findData(whisper))
        model_controls.addWidget(self.combo_whisper, 1)
        layout.addLayout(model_controls)
        self.combo_stt_language = QComboBox()
        for label, code in (("Українська · Gemini Live / Whisper", "uk"),
                            ("Русский · Whisper", "ru"), ("English · Whisper", "en"),
                            ("Auto · Whisper", "auto")):
            self.combo_stt_language.addItem(label, code)
        self.combo_stt_language.setCurrentIndex(max(
            0, self.combo_stt_language.findData(self.config_mgr.get("whisper_language", "uk"))
        ))
        layout.addWidget(self.combo_stt_language)

        self.lbl_set_source = QLabel("ДЖЕРЕЛО АУДІО:")
        layout.addWidget(self.lbl_set_source)
        
        self.combo_audio_source = QComboBox()
        self.combo_audio_source.addItems(["Системний звук (Loopback)", "Мікрофон"])
        current_mode = self.config_mgr.get("audio_mode", "loopback")
        self.combo_audio_source.setCurrentIndex(0 if current_mode == "loopback" else 1)
        layout.addWidget(self.combo_audio_source)

        self.lbl_set_device = QLabel("АУДІОПРИСТРІЙ:")
        layout.addWidget(self.lbl_set_device)
        
        self.combo_device_select = QComboBox()
        layout.addWidget(self.combo_device_select)
        self.combo_audio_source.currentIndexChanged.connect(self.refresh_audio_devices)

        self.btn_save_cfg = QPushButton("ЗБЕРЕГТИ НАЛАШТУВАННЯ")
        self.btn_save_cfg.setFixedHeight(42)
        self.btn_save_cfg.setObjectName("BtnPrimary")
        self.btn_save_cfg.clicked.connect(self.save_settings)
        layout.addWidget(self.btn_save_cfg)
        layout.addStretch()

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
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("Введи термін для пошуку...")
        self.input_search.setFixedHeight(34)
        
        self.btn_search = QPushButton("ЗНАЙТИ")
        self.btn_search.setFixedHeight(34)
        self.btn_search.setObjectName("BtnPrimary")
        self.btn_search.clicked.connect(lambda: self.search_keyword_signal.emit(self.input_search.text()))

        self.btn_summary = QPushButton("АВТОКОНСПЕКТ")
        self.btn_summary.setFixedHeight(34)
        self.btn_summary.setObjectName("BtnPrimary")
        self.btn_summary.clicked.connect(lambda: self.auto_summary_signal.emit(self.list_docs.currentItem().text() if self.list_docs.currentItem() else ""))

        tools_layout.addWidget(self.input_search)
        tools_layout.addWidget(self.btn_search)
        tools_layout.addWidget(self.btn_summary)
        layout.addLayout(tools_layout)

        self.list_docs = QListWidget()
        self.list_docs.setMaximumHeight(80)
        layout.addWidget(self.list_docs)

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

    def apply_monochrome_theme(self):
        mono_css = """
        #ExamAIWindow {
            background-color: #0a0a0a;
            border: none;
        }
        #CentralContainer {
            background-color: rgba(10, 10, 10, 0.97);
            border: 1px solid rgba(255, 255, 255, 0.09);
            border-radius: 14px;
        }
        QWidget {
            color: #dcdcdc;
            font-family: 'Segoe UI';
            font-weight: 600;
            font-size: 13px;
        }
        #LangContainer {
            background-color: #161616;
            border: 1px solid rgba(255, 255, 255, 0.07);
            border-radius: 12px;
        }
        #LangBtn {
            background-color: transparent;
            color: #777777;
            border: none;
            border-radius: 9px;
            font-size: 11px;
            font-weight: 800;
        }
        #LangBtn:hover {
            color: #ffffff;
        }
        #LangBtnActive {
            background-color: #ffffff;
            color: #000000;
            border: none;
            border-radius: 9px;
            font-size: 11px;
            font-weight: 800;
        }
        QTextEdit, QLineEdit, QListWidget {
            background-color: #131313;
            color: #ffffff;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px;
            padding: 9px;
            font-weight: 600;
        }
        QTextEdit:focus, QLineEdit:focus {
            border: 1px solid rgba(255, 255, 255, 0.3);
        }
        QComboBox {
            background-color: #131313;
            color: #ffffff;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px;
            padding: 6px 12px;
            font-weight: 600;
        }
        QComboBox QAbstractItemView {
            background-color: #131313;
            color: #ffffff;
            selection-background-color: #222222;
            border: 1px solid rgba(255, 255, 255, 0.08);
        }
        QPushButton {
            background-color: #171717;
            color: #d1d1d1;
            border: 1px solid rgba(255, 255, 255, 0.07);
            border-radius: 8px;
            font-weight: 700;
        }
        QPushButton:hover {
            background-color: #222222;
            color: #ffffff;
            border: 1px solid rgba(255, 255, 255, 0.15);
        }
        #BtnPrimary {
            background-color: #ffffff;
            color: #000000;
            border: none;
            font-weight: 800;
        }
        #BtnPrimary:hover {
            background-color: #dcdcdc;
        }
        #BtnSecondary {
            background-color: #1c1c1c;
            color: #ffffff;
            border: 1px solid rgba(255, 255, 255, 0.08);
            font-weight: 800;
        }
        #BtnListenStart {
            background-color: #ffffff;
            color: #000000;
            border: none;
            font-weight: 800;
            font-size: 14px;
        }
        #BtnListenStop {
            background-color: #1b1313;
            color: #ff5555;
            border: 2px solid #442222;
            font-weight: 800;
            font-size: 14px;
        }
        #BtnDanger {
            background-color: #1a0f0f;
            color: #ff4d4d;
            border: 1px solid #331414;
        }
        #SmallBtn {
            background-color: transparent;
            color: #777777;
            border: none;
            font-size: 11px;
            text-decoration: underline;
        }
        #SmallBtn:hover {
            color: #ffffff;
        }
        #HeaderLabel {
            color: #777777;
            font-weight: 800;
            font-size: 11px;
            letter-spacing: 1px;
        }
        #HeaderLabelActive {
            color: #ffffff;
            font-weight: 800;
            font-size: 11px;
            letter-spacing: 1px;
        }
        #WindowBtn, #CloseBtn {
            background: transparent;
            border: none;
            color: #777777;
        }
        #CloseBtn:hover {
            color: #ff3333;
        }
        QTabWidget::pane {
            border: none;
            background: transparent;
        }
        QTabBar::tab {
            background: transparent;
            color: #666666;
            padding: 8px 16px;
            font-weight: 800;
            font-size: 13px;
            margin-right: 4px;
            border-bottom: 2px solid transparent;
        }
        QTabBar::tab:selected {
            color: #ffffff;
            border-bottom: 2px solid #ffffff;
        }
        #ToastLabel {
            background-color: #1a1a1a;
            color: #00ff88;
            border: 1px solid #333333;
            border-radius: 8px;
            font-weight: 800;
            font-size: 12px;
            padding: 6px 12px;
        }
        """
        self.setStyleSheet(mono_css)
        self.txt_answer.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.txt_question.setFont(QFont("Segoe UI", 12, QFont.Bold))

    @Slot()
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
        self.btn_toggle_listen.setObjectName("BtnListenStop" if enabled else "BtnListenStart")
        self.btn_toggle_listen.style().unpolish(self.btn_toggle_listen)
        self.btn_toggle_listen.style().polish(self.btn_toggle_listen)
        self.btn_toggle_listen.setEnabled(True)

    def copy_answer(self):
        text = self.txt_answer.copyable_text()
        if text:
            QGuiApplication.clipboard().setText(text)
            copied_txt = "Скопійовано!" if self.tr.current_lang == "UA" else "Copied!"
            self.lbl_status.setText(copied_txt)

    def clear_both(self):
        self.txt_question.clear()
        self.txt_answer.clear()
        self.clear_all_signal.emit()

    def closeEvent(self, event):
        event.ignore()
        self.close_requested.emit()

    def upload_file(self):
        title = "Виберіть конспект" if self.tr.current_lang == "UA" else "Select note"
        file_path, _ = QFileDialog.getOpenFileName(
            self, title, "", "Documents (*.pdf *.docx *.txt)",
            options=QFileDialog.Option.DontUseNativeDialog,
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
                "audio_mode": selected_mode,
                "audio_device_index": self.combo_device_select.currentData(),
                "language": self.tr.current_lang,
            })
        except OSError:
            self.lbl_toast.setText("Помилка збереження / Save error")
        else:
            self.show_toast(self.tr.get("toast_saved"))

    @Slot(float)
    def update_audio_indicator(self, rms_val: float):
        self.audio_visualizer.set_rms(rms_val)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and hasattr(self, '_drag_pos'):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
