"""Themed subscription cards and catalog-backed sandbox actions."""

from PySide6.QtCore import QEasingCurve, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from examai.ui.appearance import blend
from examai.ui.translator import TRANSLATIONS
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)


PLANS = ((1, 149), (3, 399), (6, 699), (12, 1299))
FEATURES = ("sub_feature_ai", "sub_feature_screen", "sub_feature_pdf", "sub_feature_history")


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
        self.color = QColor("#ac9cff")

    def set_color(self, color):
        self.color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self.color, 2.1, Qt.PenStyle.SolidLine,
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
        self.color = QColor("#b6d8ff")

    def set_color(self, color):
        self.color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.color, 1.9, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(3, 10, 8, 15)
        painter.drawLine(8, 15, 17, 5)


class PlanTile(QWidget):
    def __init__(self, months, price, *, featured=False):
        super().__init__()
        self.months = months
        self.price_uah = price
        self.featured = featured
        self.setMinimumWidth(145)
        self.setMinimumHeight(295)
        outer = QVBoxLayout(self)
        self.outer = outer
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        self.badge = None
        if featured:
            self.badge = QLabel()
            self.badge.setObjectName("PlanBadge")
            self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.badge.setFixedSize(128, 24)
            outer.addWidget(self.badge, alignment=Qt.AlignmentFlag.AlignHCenter)
        else:
            outer.addSpacing(24)
        self.card = card = QFrame()
        card.setObjectName("PlanFeatured" if featured else "PlanCard")
        outer.addWidget(card, 1)
        body = QVBoxLayout(card)
        body.setContentsMargins(13, 17, 13, 12)
        body.setSpacing(8)
        self.term_label = QLabel()
        self.term_label.setObjectName("PlanTerm")
        self.term_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.term_label.setWordWrap(True)
        body.addWidget(self.term_label)
        self.price_label = QLabel()
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
        self.check_icons = []
        for _feature in FEATURES:
            line = QHBoxLayout()
            line.setSpacing(6)
            check = CheckIcon()
            line.addWidget(check)
            self.check_icons.append(check)
            label = QLabel()
            label.setObjectName("PlanFeature")
            label.setWordWrap(True)
            line.addWidget(label, 1)
            body.addLayout(line)
            self.features.append(label)
        body.addStretch()
        self.order_button = QPushButton()
        self.order_button.setObjectName("PlanOrderFeatured" if featured else "PlanOrder")
        self.order_button.setMinimumHeight(43)
        self.order_button.setEnabled(False)
        body.addWidget(self.order_button)


    def paint_highlight(self, value):
        if self.hover_colors is None:
            return
        self.hover_value = float(value)
        host = self.window()
        lift = round(3 * value) if hasattr(host, "motion_ms") and host.motion_ms(170) else 0
        self.outer.setContentsMargins(0, 3 - lift, 0, lift)
        surface, border, accent = self.hover_colors
        glow = blend(surface, accent, 0.12 * value)
        edge = blend(border, accent, value)
        self.card.setStyleSheet(
            f"QFrame#{self.card.objectName()} {{ background: {glow}; border: "
            f"{2 if self.featured else 1}px solid {edge}; border-radius: 16px; }}"
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

    def __init__(self, account=None, open_account=None):
        super().__init__()
        self.account = account
        self.open_account = open_account
        if account is not None:
            account.catalog_changed.connect(self.refresh_checkout)
        self.setObjectName("SubscriptionsPage")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._built = False
        self.cards = []
        self._columns = 0
        self.language = "UA"
        self._theme = "midnight"
        self._accent = "#8e93ff"
        self._plan_prices = {months: price for months, price in PLANS}

    def _build(self):
        if self._built:
            return
        self.backdrop = SubscriptionBackdrop()
        self.setWidget(self.backdrop)
        root = QVBoxLayout(self.backdrop)
        root.setContentsMargins(15, 14, 15, 10)
        root.setSpacing(10)
        self.heading = QLabel()
        self.heading.setObjectName("SubscriptionHeading")
        root.addWidget(self.heading)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("SubscriptionSubtitle")
        self.subtitle.setWordWrap(True)
        root.addWidget(self.subtitle)
        status = QFrame()
        status.setObjectName("SubscriptionStatus")
        status_row = QHBoxLayout(status)
        status_row.setContentsMargins(16, 10, 16, 10)
        status_row.setSpacing(12)
        self.crown = CrownIcon()
        status_row.addWidget(self.crown)
        self.status_label = QLabel()
        self.status_label.setObjectName("SubscriptionStatusText")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        if self.open_account is not None:
            self.account_button = QPushButton("Обліковий запис")
            self.account_button.clicked.connect(self.open_account)
            status_row.addWidget(self.account_button)
        root.addWidget(status)
        root.addSpacing(4)
        self.cards = [PlanTile(months, price, featured=index == 1)
                      for index, (months, price) in enumerate(PLANS)]
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
        self.set_language(self.language)
        self.apply_theme(self._theme, self._accent)

    def refresh_checkout(self):
        if not self._built or self.account is None:
            return
        catalog = self.account.available_plans
        for months, card in zip((1, 3, 6, 12), self.cards):
            plan = catalog.get(str(months))
            if plan:
                self._plan_prices[months] = plan["price_uah"]
            enabled = bool(plan and self.account.checkout.isEnabled())
            card.order_button.setEnabled(enabled)
        self.set_language(self.language)

    def apply_theme(self, theme, accent):
        self._theme, self._accent = theme, accent
        if not self._built:
            return
        from examai.ui.appearance import THEMES
        _, base, surface, border, fg, muted, active = THEMES.get(theme, THEMES["midnight"])
        if theme == "custom" and QColor(accent).isValid():
            active = accent
        self.crown.set_color(active)
        for card in self.cards:
            for check in card.check_icons:
                check.set_color(active)
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
            #PlanBadge {{ background: {active}; color: {base}; border: 1px solid {active};
                border-radius: 11px; font-size: 11px; font-weight: 700; }}
            #PlanOrder, #PlanOrderFeatured {{ border-radius: 12px; padding: 6px; }}
            #PlanOrder:disabled, #PlanOrderFeatured:disabled {{ color: {muted};
                background: {surface}; border: 1px solid {border}; }}
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
        self.language = language if language in TRANSLATIONS else "UA"
        if hasattr(self, "account_button"):
            self.account_button.setText("Account" if self.language == "ENG" else "Обліковий запис")
        if not self._built:
            return
        words = TRANSLATIONS[self.language]
        self.heading.setText(words["sub_title"])
        self.subtitle.setText(words["sub_description"])
        self.status_label.setText(words["sub_status"])
        for card in self.cards:
            card.term_label.setText(words[f"sub_month_{card.months}"])
            card.price_label.setText(words["sub_price"].format(
                amount=self._plan_prices[card.months]))
            for key, label in zip(FEATURES, card.features):
                label.setText(words[key])
            if card.badge is not None:
                card.badge.setText(words["sub_best"])
            available = card.order_button.isEnabled()
            card.order_button.setText(words["sub_checkout"] if available and self.account is None
                                      else words["sub_test_checkout"] if available
                                      else words["sub_unavailable"])
            card.order_button.setToolTip(words["sub_test_checkout"] if available
                                         else words["sub_tip_unavailable"])
