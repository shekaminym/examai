"""Themed subscription cards and catalog-backed sandbox actions."""

from PySide6.QtCore import QEasingCurve, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from examai.ui.appearance import blend
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
        from examai.ui.appearance import THEMES
        theme = getattr(self.window(), "config_mgr", None)
        theme = theme.get("theme") if theme else "midnight"
        painter.fillRect(self.rect(), QColor(THEMES.get(theme, THEMES["midnight"])[1]))


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
        self.outer = outer
        outer.setContentsMargins(0, 15, 0, 0)
        self.card = card = QFrame()
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
        self.hover_animation = QVariantAnimation(self)
        self.hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.hover_animation.valueChanged.connect(self.paint_highlight)
        self.hover_colors = None
        self.hover_value = 0.0
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


    def paint_highlight(self, value):
        if self.hover_colors is None:
            return
        self.hover_value = float(value)
        host = self.window()
        lift = round(3 * value) if hasattr(host, "motion_ms") and host.motion_ms(170) else 0
        self.outer.setContentsMargins(0, 15 - lift, 0, lift)
        surface, border, accent = self.hover_colors
        glow = blend(surface, accent, 0.12 * value)
        edge = blend(border, accent, value)
        self.card.setStyleSheet(
            f"QFrame#{self.card.objectName()} {{ background: {glow}; border: 1px solid {edge}; border-radius: 16px; }}"
        )

    def set_hover(self, target):
        host = self.window()
        duration = host.motion_ms(170) if hasattr(host, "motion_ms") else 0
        self.hover_animation.stop()
        if not duration or self.hover_colors is None:
            self.paint_highlight(target)
            return
        self.hover_animation.setStartValue(self.hover_value)
        self.hover_animation.setEndValue(target)
        self.hover_animation.setDuration(duration)
        self.hover_animation.start()

    def enterEvent(self, event):
        self.set_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.set_hover(0.0)
        super().leaveEvent(event)


class SubscriptionsPage(QScrollArea):
    """Four visual price cards, reflowed to two columns on narrow windows."""

    def __init__(self, account=None):
        super().__init__()
        self.account = account
        if account is not None:
            account.catalog_changed.connect(self.refresh_checkout)
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
        if self.account is not None:
            for index, card in enumerate(self.cards):
                plan_id = str((1, 3, 6, 12)[index])
                card.order_button.clicked.connect(lambda checked=False, plan=plan_id: self.account.open_checkout(plan))
            self.refresh_checkout()
        config = getattr(self.window(), "config_mgr", None)
        if config:
            self.apply_theme(config.get("theme"), config.get("accent"))

    def refresh_checkout(self):
        if not self._built or self.account is None:
            return
        catalog = self.account.available_plans
        for months, card in zip((1, 3, 6, 12), self.cards):
            plan = catalog.get(str(months))
            if plan:
                card.price_label.setText(f"{plan['price_uah']} грн")
            enabled = bool(plan and self.account.checkout.isEnabled())
            card.order_button.setText("LiqPay · тест" if enabled else "Недоступно")
            card.order_button.setEnabled(enabled)
            card.order_button.setToolTip("Тестова оплата" if enabled else "Оформлення стане доступним пізніше")

    def apply_theme(self, theme, accent):
        if not self._built:
            return
        from examai.ui.appearance import THEMES
        _, base, surface, border, fg, muted, active = THEMES.get(theme, THEMES["midnight"])
        if theme == "custom" and QColor(accent).isValid():
            active = accent
        for card in self.cards:
            card.hover_colors = surface, active if card.featured else border, active
            card.hover_animation.stop()
            card.paint_highlight(0.0)
            motion = getattr(self.window(), "motion", None)
            if motion:
                motion.register(card.hover_animation)
        self.setStyleSheet(f"""
            #SubscriptionsPage {{ background: {base}; border: none; }}
            #PlanCard, #PlanFeatured, #SubscriptionStatus {{
                background: {surface}; border: 1px solid {border}; border-radius: 16px;
            }}
            #PlanFeatured {{ border: 1px solid {active}; }}
            QLabel {{ color: {fg}; }}
            #SubscriptionHeading {{ font-size: 23px; font-weight: 700; }}
            #SubscriptionSubtitle {{ color: {muted}; }}
            #PlanPrice, #PlanPriceFeatured {{ font-size: 20px; font-weight: 700; }}
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
