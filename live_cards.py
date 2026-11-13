"""Live transcript and streaming answer within the compact dock's HWND."""
from time import perf_counter

from PySide6.QtCore import QAbstractAnimation, QEvent, QTimer, Qt
from PySide6.QtGui import QIcon, QPainter, QPen, QPixmap
from response_view import ResponseView
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from performance import timing


class LiveCards(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.request_id = None
        self.history_total = 0
        self.expanded = False
        self.dismissed = False
        self.success = None
        self.first_emitted = None
        self.layout_box = QVBoxLayout(self)
        self.layout_box.setContentsMargins(0, 0, 0, 0)
        self.layout_box.setSpacing(5)
        self.question, self.question_status, self.question_text, question_tools = self.card("Питання")
        self.answer, self.answer_status, self.answer_text, answer_tools = self.card("Відповідь EXAM AI", rich=True)
        self.send = self.button(question_tools, "Надіслати", "Надіслати питання",
                                lambda: window.question_submitted.emit(self.question_text.toPlainText()))
        self.edit_draft = self.button(question_tools, "Виправити", "Виправити чернетку",
                                      self.open_draft)
        self.edit_draft.hide()
        self.button(question_tools, "×", "Сховати картки", self.dismiss)
        self.previous = self.button(answer_tools, "", "Попередня відповідь",
                                    lambda: window.history_step_signal.emit(-1))
        self.latest = self.button(answer_tools, "", "Остання відповідь",
                                  lambda: window.history_step_signal.emit(self.history_total))
        for button, direction in ((self.previous, -1), (self.latest, 1)):
            button.setFixedWidth(25)
            button.setProperty("arrow_direction", direction)
        self.copy = self.button(answer_tools, "Копіювати", "Копіювати відповідь",
                                lambda: QApplication.clipboard().setText(self.answer_text.copyable_text()))
        self.expand = self.button(answer_tools, "▾", "Розгорнути відповідь", self.toggle_expanded)
        self.history = self.button(answer_tools, "Історія", "Відкрити історію", self.open_history)
        self.previous.setEnabled(False)
        self.latest.setEnabled(False)
        self.loading_timer = QTimer(self, interval=230)
        self.loading_timer.timeout.connect(self.tick_loading)
        self.answer_status.setMinimumWidth(84)
        self.resize_timer = QTimer(self, singleShot=True, interval=90)
        self.resize_timer.timeout.connect(lambda: self.fit(animate=True))
        self.answer_text.setUndoRedoEnabled(False)
        self.answer_text.viewport().installEventFilter(self)
        self.clear()

    def card(self, title, *, rich=False):
        frame = QFrame()
        frame.setObjectName("LiveCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(9, 4, 9, 5)
        layout.setSpacing(3)
        tools = QHBoxLayout()
        tools.setSpacing(7)
        heading = QLabel(title)
        heading.setObjectName("HeaderLabel")
        heading.setProperty("source_label", title)
        tools.addWidget(heading)
        status = QLabel()
        status.setObjectName("HeaderLabel")
        tools.addWidget(status, 1)
        layout.addLayout(tools)
        body = ResponseView() if rich else QPlainTextEdit()
        body.setObjectName("LiveCardText")
        body.setReadOnly(True)
        body.setFrameShape(QFrame.Shape.NoFrame)
        body.setFixedHeight(36)
        layout.addWidget(body)
        self.layout_box.addWidget(frame)
        return frame, status, body, tools

    @staticmethod
    def button(layout, text, tooltip, callback):
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setProperty("source_label", text)
        button.setProperty("source_tip", tooltip)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def retranslate(self):
        tr = self.window.tr.local
        for label in self.findChildren(QLabel):
            source = label.property("source_label")
            if source is not None:
                label.setText(tr(source))
        for button in self.findChildren(QPushButton):
            label = button.property("source_label")
            tooltip = button.property("source_tip")
            if label and button is not self.expand:
                button.setText(tr(label))
            if tooltip:
                button.setToolTip(tr(tooltip))
                button.setAccessibleName(tr(tooltip))

    def update_icons(self, color):
        for button in (self.previous, self.latest):
            direction = button.property("arrow_direction")
            pixmap = QPixmap(28, 28)
            pixmap.setDevicePixelRatio(2)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(7 - 2 * direction, 3, 7 + 2 * direction, 7)
            painter.drawLine(7 + 2 * direction, 7, 7 - 2 * direction, 11)
            painter.end()
            button.setIcon(QIcon(pixmap))

    def set_status(self, label, source):
        label.setProperty("source_label", source)
        label.setText(self.window.tr.local(source))

    def preview(self, text, *, new=False, status="Слухає…"):
        if not text.strip():
            return
        if new or self.question.isHidden():
            self.dismissed = False
            self.request_id = None
            self.answer.hide()
        if self.dismissed:
            return
        if self.question_text.toPlainText() != text:
            self.question_text.setPlainText(text)
        self.edit_draft.hide()
        self.send.setVisible(not self.window.auto_send.isChecked())
        self.set_status(self.question_status, status)
        self.question_status.setToolTip("")
        self.question.show()
        self.schedule_fit()

    def review(self, text):
        self.preview(text, status="Перевірте текст")
        self.send.show()
        self.edit_draft.show()

    def open_draft(self):
        if self.window.window_mode.collapsed:
            self.window.window_mode.expand_toggle()
        self.window.tabs.setCurrentWidget(self.window.tab_live)
        editor = self.window.txt_question
        editor.setPlainText(self.question_text.toPlainText())
        animation = self.window.window_mode.animation
        if animation.state() == QAbstractAnimation.State.Running:
            animation.finished.connect(editor.setFocus, Qt.ConnectionType.SingleShotConnection)
        else:
            editor.setFocus()

    def stop_listening(self):
        if self.request_id is None and self.question_status.property("source_label") == "Слухає…":
            self.set_status(self.question_status, "")

    def tick_loading(self):
        if not self.isVisible() or not self.window.motion_ms(180):
            return
        dots = self.answer_status.text().count("·") % 3 + 1
        self.answer_status.setText("EXAM AI " + "·" * dots)

    def begin(self, request_id, question):
        self.request_id = request_id
        self.dismissed = False
        self.first_emitted = None
        self.preview(question, status="Аналізує…")
        self.send.hide()
        self.answer_text.setPlainText("")
        self.set_status(self.answer_status, "EXAM AI ···")
        if self.window.motion_ms(180):
            self.loading_timer.start()
        self.answer_status.setToolTip(self.window.tr.local("EXAM AI аналізує…"))
        self.answer.show()
        self.fit(animate=True)

    def progress(self, request_id, text, emitted=None):
        if request_id != self.request_id or not text:
            return
        self.loading_timer.stop()
        first = not self.answer_text.source
        self.answer_text.show_streaming_response(text)
        self.set_status(self.question_status, "")
        self.set_status(self.answer_status, "")
        self.answer_status.setToolTip("")
        if first and not self.dismissed:
            self.fit()
            if emitted is not None and self.isVisible():
                self.first_emitted = emitted
                self.answer_text.viewport().update()
        else:
            self.schedule_fit()

    def ready(self, request_id, question, answer, *, history=False):
        if not history and request_id != self.request_id:
            return
        self.loading_timer.stop()
        if not history:
            self.success = (question, answer)
        if history:
            self.dismissed = False
            self.request_id = request_id
        self.preview(question, status="")
        self.answer_text.show_response(answer, streaming=True)
        self.send.hide()
        self.set_status(self.answer_status, "Готово")
        self.answer_status.setToolTip("")
        self.answer.show()
        self.fit(animate=True)

    def error(self, request_id, message):
        if request_id != self.request_id:
            return
        self.loading_timer.stop()
        self.set_status(self.question_status, "Помилка")
        self.question_status.setToolTip(message)
        if self.success:
            question, answer = self.success
            self.answer_text.show_response(answer, streaming=True)
            self.set_status(self.answer_status, "Попередня відповідь")
            self.answer_status.setToolTip(question)
        else:
            self.answer_text.setPlainText(message)
            self.set_status(self.answer_status, "Помилка")
        self.schedule_fit()

    def clear(self):
        self.loading_timer.stop()
        self.request_id = None
        self.question_text.clear()
        self.answer_text.setPlainText("")
        self.question.hide()
        self.answer.hide()
        self.success = None
        self.dismissed = False
        self.first_emitted = None
        self.fit()

    def dismiss(self):
        self.dismissed = True
        self.first_emitted = None
        self.fit(animate=True)

    def reopen(self):
        if self.dismissed:
            self.dismissed = False
            self.fit(animate=True)

    def toggle_expanded(self):
        self.expanded = not self.expanded
        self.expand.setText("▴" if self.expanded else "▾")
        self.expand.setProperty("source_tip", "Згорнути відповідь" if self.expanded else "Розгорнути відповідь")
        self.retranslate()
        self.fit(animate=True)

    def open_history(self):
        if self.window.window_mode.collapsed:
            self.window.window_mode.expand_toggle()
        self.window.tabs.setCurrentWidget(self.window.tab_history)

    def schedule_fit(self):
        if not self.resize_timer.isActive():
            self.resize_timer.start()

    def fit(self, animate=False):
        if hasattr(self.window, "window_mode"):
            self.window.window_mode.fit_cards(animate=animate)

    def height_for(self, budget):
        if self.dismissed or self.question.isHidden() or budget < 70:
            return 0
        self.layout_box.activate()
        if not self.answer.isHidden() and budget < 135:
            return 0
        budget = min(budget, 440 if self.expanded else 255)
        question_height = min(62, self.text_height(self.question_text))
        self.question_text.setFixedHeight(question_height)
        if not self.answer.isHidden():
            available = budget - self.question.sizeHint().height() - 36
            self.answer_text.setFixedHeight(max(22, min(available,
                360 if self.expanded else 135, self.text_height(self.answer_text))))
        return min(budget, self.sizeHint().height())

    @staticmethod
    def text_height(body):
        height = body.document().size().height()
        if isinstance(body, QPlainTextEdit):
            height *= body.fontMetrics().lineSpacing()
        return max(28, int(height) + 14)

    def eventFilter(self, watched, event):
        if watched is self.answer_text.viewport() and event.type() == QEvent.Type.Paint and self.first_emitted is not None:
            timing.info("request_id=%s dock_first_paint_ms=%.2f",
                        self.request_id, (perf_counter() - self.first_emitted) * 1000)
            self.first_emitted = None
        return super().eventFilter(watched, event)
