"""Live transcript and streaming answer within the compact dock's HWND."""
from time import perf_counter

from PySide6.QtCore import QAbstractAnimation, QEvent, QTimer, Qt
from PySide6.QtGui import QTextCursor
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
        self.answer, self.answer_status, self.answer_text, answer_tools = self.card("Відповідь EXAM AI")
        self.send = self.button(question_tools, "Надіслати", "Надіслати питання",
                                lambda: window.question_submitted.emit(self.question_text.toPlainText()))
        self.edit_draft = self.button(question_tools, "Виправити", "Виправити чернетку",
                                      self.open_draft)
        self.edit_draft.hide()
        self.button(question_tools, "×", "Сховати картки", self.dismiss)
        self.previous = self.button(answer_tools, "‹", "Попередня відповідь",
                                    lambda: window.history_step_signal.emit(-1))
        self.latest = self.button(answer_tools, "›|", "Остання відповідь",
                                  lambda: window.history_step_signal.emit(self.history_total))
        self.copy = self.button(answer_tools, "Копіювати", "Копіювати відповідь",
                                lambda: QApplication.clipboard().setText(self.answer_text.toPlainText()))
        self.expand = self.button(answer_tools, "▾", "Розгорнути відповідь", self.toggle_expanded)
        self.history = self.button(answer_tools, "Історія", "Відкрити історію", self.open_history)
        self.previous.setEnabled(False)
        self.latest.setEnabled(False)
        self.resize_timer = QTimer(self, singleShot=True, interval=90)
        self.resize_timer.timeout.connect(lambda: self.fit(animate=True))
        self.answer_text.setUndoRedoEnabled(False)
        self.answer_text.viewport().installEventFilter(self)
        self.clear()

    def card(self, title):
        frame = QFrame()
        frame.setObjectName("LiveCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(9, 4, 9, 5)
        layout.setSpacing(3)
        tools = QHBoxLayout()
        tools.setSpacing(7)
        heading = QLabel(title)
        heading.setObjectName("HeaderLabel")
        tools.addWidget(heading)
        status = QLabel()
        status.setObjectName("HeaderLabel")
        tools.addWidget(status, 1)
        layout.addLayout(tools)
        body = QPlainTextEdit()
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
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

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
        self.question_status.setText(status)
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
        if self.request_id is None and self.question_status.text() == "Слухає…":
            self.question_status.clear()

    def begin(self, request_id, question):
        self.request_id = request_id
        self.dismissed = False
        self.first_emitted = None
        self.preview(question, status="Аналізує…")
        self.send.hide()
        self.answer_text.clear()
        self.answer_status.setText("Очікування…")
        self.answer_status.setToolTip("")
        self.answer.show()
        self.fit(animate=True)

    def progress(self, request_id, text, emitted=None):
        if request_id != self.request_id or not text:
            return
        first = not self.answer_text.toPlainText()
        self.write_answer(text)
        self.question_status.clear()
        self.answer_status.setText("Генерує…")
        if first and not self.dismissed:
            self.fit()
            if emitted is not None and self.isVisible():
                self.first_emitted = emitted
                self.answer_text.viewport().update()
        else:
            self.schedule_fit()

    def write_answer(self, text):
        body = self.answer_text
        old = body.toPlainText()
        if old == text:
            return
        bar = body.verticalScrollBar()
        position = bar.value()
        follow = position >= bar.maximum()
        if old and text.startswith(old):
            cursor = QTextCursor(body.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(text[len(old):])
        else:
            body.setPlainText(text)
        bar.setValue(bar.maximum() if follow else position)

    def ready(self, request_id, question, answer, *, history=False):
        if not history and request_id != self.request_id:
            return
        if not history:
            self.success = (question, answer)
        if history:
            self.dismissed = False
            self.request_id = request_id
        self.preview(question, status="")
        self.write_answer(answer)
        self.send.hide()
        self.answer_status.setText("Готово")
        self.answer_status.setToolTip("")
        self.answer.show()
        self.fit(animate=True)

    def error(self, request_id, message):
        if request_id != self.request_id:
            return
        self.question_status.setText("Помилка")
        self.question_status.setToolTip(message)
        if self.success:
            question, answer = self.success
            self.write_answer(answer)
            self.answer_status.setText("Попередня відповідь")
            self.answer_status.setToolTip(question)
        else:
            self.write_answer(message)
            self.answer_status.setText("Помилка")
        self.schedule_fit()

    def clear(self):
        self.request_id = None
        self.question_text.clear()
        self.answer_text.clear()
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
        self.expand.setToolTip("Згорнути відповідь" if self.expanded else "Розгорнути відповідь")
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
        return max(28, int(body.document().size().height() * body.fontMetrics().lineSpacing()) + 14)

    def eventFilter(self, watched, event):
        if watched is self.answer_text.viewport() and event.type() == QEvent.Type.Paint and self.first_emitted is not None:
            timing.info("request_id=%s dock_first_paint_ms=%.2f",
                        self.request_id, (perf_counter() - self.first_emitted) * 1000)
            self.first_emitted = None
        return super().eventFilter(watched, event)
