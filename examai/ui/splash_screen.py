# Файл: splash_screen.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve

class ModernSpinner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(50, 50)
        self._angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rotate)
        self.timer.start(15)

    def rotate(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(255, 255, 255, 200))
        pen.setWidth(4)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(5, 5, 40, 40, -self._angle * 16, 140 * 16)


class SplashScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SplashScreen)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(450, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.bg_frame = QWidget(self)
        self.bg_frame.setStyleSheet("""
            QWidget {
                background-color: rgba(15, 15, 15, 230);
                border-radius: 20px;
                border: 1px solid rgba(255, 255, 255, 20);
            }
        """)
        
        bg_layout = QVBoxLayout(self.bg_frame)
        bg_layout.setAlignment(Qt.AlignCenter)

        self.title_label = QLabel("EXAM AI", self.bg_frame)
        self.title_label.setStyleSheet("""
            color: white; 
            font-family: 'Cairo Black'; 
            font-size: 42px; 
            font-weight: bold; 
            background: transparent;
            border: none;
        """)
        self.title_label.setAlignment(Qt.AlignCenter)

        self.slogan_label = QLabel("LET'S CHEAT", self.bg_frame)
        self.slogan_label.setStyleSheet("""
            color: rgba(255, 255, 255, 120); 
            font-family: 'Cairo Black'; 
            font-size: 14px; 
            letter-spacing: 10px; 
            background: transparent;
            border: none;
        """)
        self.slogan_label.setAlignment(Qt.AlignCenter)

        self.spinner = ModernSpinner(self.bg_frame)

        bg_layout.addStretch()
        bg_layout.addWidget(self.title_label)
        bg_layout.addWidget(self.slogan_label)
        bg_layout.addSpacing(40)
        bg_layout.addWidget(self.spinner, alignment=Qt.AlignCenter)
        bg_layout.addStretch()

        layout.addWidget(self.bg_frame)

        self.setWindowOpacity(0.0)

        self.fade_in_anim = QPropertyAnimation(self, b"windowOpacity")
        self.fade_in_anim.setDuration(1200)
        self.fade_in_anim.setStartValue(0.0)
        self.fade_in_anim.setEndValue(1.0)
        self.fade_in_anim.setEasingCurve(QEasingCurve.InOutQuad)

        self.fade_out_anim = QPropertyAnimation(self, b"windowOpacity")
        self.fade_out_anim.setDuration(1000)
        self.fade_out_anim.setStartValue(1.0)
        self.fade_out_anim.setEndValue(0.0)
        self.fade_out_anim.setEasingCurve(QEasingCurve.InOutQuad)
        
        self.fade_out_anim.finished.connect(self.close) 

    def start_splash(self):
        self.show()
        self.fade_in_anim.start()
        QTimer.singleShot(3500, self.fade_out_anim.start)
