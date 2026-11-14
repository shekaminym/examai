"""Static subscription artwork. No purchase, account or API integration."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)


PLANS = (
    ("1 місяць", "149 грн"),
    ("3 місяці", "399 грн"),
    ("6 місяців", "699 грн"),
    ("12 місяців", "1299 грн"),
)
FEATURES = ("Доступ до AI", "Аналіз екрана", "Робота з PDF", "Історія")


class SubscriptionBackdrop(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        base = QLinearGradient(0, 0, self.width(), self.height())
        base.setColorAt(0, QColor("#132657"))
        base.setColorAt(.65, QColor("#14265a"))
        base.setColorAt(1, QColor("#101d47"))
        painter.fillRect(self.rect(), base)
        width = self.width()
        wave = QPainterPath()
        wave.moveTo(width * .49, 0)
        wave.cubicTo(width * .67, 105, width * .71, 26, width, 12)
        wave.lineTo(width, 0)
        wave.closeSubpath()
        glow = QLinearGradient(width * .55, 0, width, 110)
        glow.setColorAt(0, QColor(80, 64, 165, 18))
        glow.setColorAt(.57, QColor(133, 78, 216, 78))
        glow.setColorAt(1, QColor(58, 115, 226, 74))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawPath(wave)
        wave = QPainterPath()
        wave.moveTo(width * .73, 0)
        wave.cubicTo(width * .83, 60, width * .87, 11, width, 9)
        wave.lineTo(width, 0)
        wave.closeSubpath()
        painter.setBrush(QColor(66, 123, 225, 82))
        painter.drawPath(wave)


class CrownIcon(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(30, 26)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#ac9cff"), 2.1, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        crown = QPainterPath()
        crown.moveTo(3, 7)
        crown.lineTo(9, 12)
        crown.lineTo(15, 3)
        crown.lineTo(21, 12)
        crown.lineTo(27, 7)
        crown.lineTo(24, 21)
        crown.lineTo(6, 21)
        crown.closeSubpath()
        painter.drawPath(crown)


class CheckIcon(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(20, 20)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#b6d8ff"), 1.9, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(3, 10, 8, 15)
        painter.drawLine(8, 15, 17, 5)


class PlanTile(QWidget):
    def __init__(self, term, price, *, featured=False):
        super().__init__()
        self.featured = featured
        self.setMinimumWidth(145)
        self.setMinimumHeight(295)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 15, 0, 0)
        card = QFrame()
        card.setObjectName("PlanFeatured" if featured else "PlanCard")
        outer.addWidget(card)
        body = QVBoxLayout(card)
        body.setContentsMargins(13, 17, 13, 12)
        body.setSpacing(8)
        self.term_label = QLabel(term)
        self.term_label.setObjectName("PlanTerm")
        self.term_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.term_label.setWordWrap(True)
        body.addWidget(self.term_label)
        self.price_label = QLabel(price)
        self.price_label.setObjectName("PlanPriceFeatured" if featured else "PlanPrice")
        self.price_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.price_label.setMinimumHeight(49)
        body.addWidget(self.price_label)
        body.addSpacing(2)
        self.features = []
        for feature in FEATURES:
            line = QHBoxLayout()
            line.setSpacing(6)
            line.addWidget(CheckIcon())
            label = QLabel(feature)
            label.setObjectName("PlanFeature")
            label.setWordWrap(True)
            line.addWidget(label, 1)
            body.addLayout(line)
            self.features.append(label)
        body.addStretch()
        self.order_button = QPushButton("Оформити")
        self.order_button.setObjectName("PlanOrderFeatured" if featured else "PlanOrder")
        self.order_button.setMinimumHeight(43)
        self.order_button.setEnabled(False)
        self.order_button.setToolTip("Оформлення стане доступним пізніше")
        body.addWidget(self.order_button)
        self.badge = None
        if featured:
            self.badge = QLabel("Найвигідніше", self)
            self.badge.setObjectName("PlanBadge")
            self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.badge.setFixedSize(126, 30)
            self.badge.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.badge is not None:
            self.badge.move((self.width() - self.badge.width()) // 2, 0)


class SubscriptionsPage(QScrollArea):
    """Four visual price cards, reflowed to two columns on narrow windows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SubscriptionsPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._built = False
        self.cards = []
        self._columns = 0

    def _build(self):
        if self._built:
            return
        self.backdrop = SubscriptionBackdrop()
        self.setWidget(self.backdrop)
        root = QVBoxLayout(self.backdrop)
        root.setContentsMargins(15, 14, 15, 10)
        root.setSpacing(10)
        self.heading = QLabel("Підписки")
        self.heading.setObjectName("SubscriptionHeading")
        root.addWidget(self.heading)
        self.subtitle = QLabel("Обери оптимальний тариф для EXAM AI")
        self.subtitle.setObjectName("SubscriptionSubtitle")
        self.subtitle.setWordWrap(True)
        root.addWidget(self.subtitle)
        status = QFrame()
        status.setObjectName("SubscriptionStatus")
        status_row = QHBoxLayout(status)
        status_row.setContentsMargins(16, 10, 16, 10)
        status_row.setSpacing(12)
        status_row.addWidget(CrownIcon())
        self.status_label = QLabel("Активна підписка:  відсутня")
        self.status_label.setObjectName("SubscriptionStatusText")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        root.addWidget(status)
        root.addSpacing(4)
        self.cards = [PlanTile(term, price, featured=index == 1)
                      for index, (term, price) in enumerate(PLANS)]
        self.cards_container = QWidget()
        self.cards_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.grid = QGridLayout(self.cards_container)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(8)
        root.addWidget(self.cards_container)
        root.addStretch()
        self._columns = 0
        self._reflow(4)
        self._built = True
        self.setStyleSheet("""
            #SubscriptionsPage { border: none; background: #132657; }
            #SubscriptionHeading { color: #f5f7ff; font: 700 30px 'Segoe UI'; }
            #SubscriptionSubtitle { color: #bdcbed; font: 14px 'Segoe UI'; }
            #SubscriptionStatus {
                background: rgba(52, 75, 129, 180); border: 1px solid #4b69a5;
                border-radius: 17px;
            }
            #SubscriptionStatusText { color: #e7edff; font: 13px 'Segoe UI'; }
            #PlanCard, #PlanFeatured {
                background: rgba(38, 60, 111, 205); border: 1px solid #516caa;
                border-radius: 20px;
            }
            #PlanFeatured {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #244a91, stop:.55 #29366d, stop:1 #513c89);
                border: 2px solid #998dff;
            }
            #PlanTerm { color: #f6f7ff; font: 700 19px 'Segoe UI'; }
            #PlanPrice, #PlanPriceFeatured {
                color: white; font: 700 21px 'Segoe UI';
                background: #344d87; border: 1px solid #5470ac;
                border-radius: 22px;
            }
            #PlanPriceFeatured {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #348ee6, stop:1 #a050e6);
                border-color: #a5aaff;
            }
            #PlanFeature { color: #dce8ff; font: 12px 'Segoe UI'; }
            #PlanOrder, #PlanOrderFeatured {
                color: #f4f7ff; font: 600 14px 'Segoe UI';
                background: #3b548f; border: 1px solid #5b78b7;
                border-radius: 20px;
            }
            #PlanOrderFeatured {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #4b90e7, stop:1 #a252e9);
                border-color: #9eafff;
            }
            #PlanBadge {
                color: white; font: 700 11px 'Segoe UI';
                background: #7956d6; border: 1px solid #a297ff;
                border-radius: 15px;
            }
        """)

    def _reflow(self, columns):
        if columns == self._columns:
            return
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)
        self._columns = columns

    def showEvent(self, event):
        self._build()
        super().showEvent(event)
        self._size_cards()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._built:
            return
        self._reflow(4 if self.viewport().width() >= 650 else 2)
        self._size_cards()

    def _size_cards(self):
        height = max(295, min(420, self.viewport().height() - 193))
        for card in self.cards:
            card.setMinimumHeight(height)

    def set_language(self, language):
        # The mockup follows the supplied Ukrainian design in both UI locales.
        if self._built:
            self.heading.setText("Підписки")
