"""Optional read-only timing window; it never polls audio or the network."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget


class DeveloperTimingWindow(QWidget):
    FIELDS = (
        ("voice", "Питання: від початку до кінця"),
        ("stt", "STT після кінця фрази"),
        ("assembly", "STT + збірка до запиту"),
        ("first_text", "Gemini: перший текст"),
        ("speech_to_first", "Від мовлення до відповіді"),
        ("complete", "Повна відповідь"),
    )

    def __init__(self):
        super().__init__()
        self.setWindowTitle("EXAM AI — заміри")
        self.setMinimumWidth(330)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setStyleSheet("""
            QWidget { background: #101d3d; color: #e5edff; font: 12px 'Segoe UI'; }
            QLabel#TimingValue { color: #9fd8ff; font-weight: 700; }
        """)
        layout = QVBoxLayout(self)
        title = QLabel("Час обробки останнього питання")
        title.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(title)
        self.values = {}
        for key, label in self.FIELDS:
            row = QHBoxLayout()
            row.addWidget(QLabel(label), 1)
            value = QLabel("—")
            value.setObjectName("TimingValue")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(value)
            layout.addLayout(row)
            self.values[key] = value
        note = QLabel("Тільки час. Без звуку, тексту питання й ключів API.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #9db0d6; font-size: 11px;")
        layout.addWidget(note)

    def reset(self):
        for label in self.values.values():
            label.setText("—")

    def set_milliseconds(self, key, value):
        if key in self.values and value is not None and value >= 0:
            self.values[key].setText(f"{value:.0f} мс")
