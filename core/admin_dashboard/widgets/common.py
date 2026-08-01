"""
widgets/common.py
------------------
Small, stateless(ish) reusable widgets shared across pages: the status dot,
the sidebar nav button, the stat card, the faint grid background, and the
CRT scanline overlay.
"""

from PySide6.QtWidgets import QWidget, QPushButton, QFrame, QLabel, QVBoxLayout, QSizePolicy
from PySide6.QtCore import Qt
from PySide6.QtGui import  QPainter, QFont, QPen, QBrush

from admin_dashboard.theme import (
    NEON_PINK, CLOUDY_SKY, PERSIAN_BLUE, TEXT_MUTED, BG_CARD, BG_PANEL, TRUE_AZURE, VIVID_ROYAL
)


class PulsingDot(QWidget):
    """Static colored dot (the animated ping/breathe version was replaced to
    cut two infinite-loop repaint animations per instance; performance over
    decoration on slower machines)."""

    def __init__(self, color=NEON_PINK, size=10, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.base_size = size
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.color)
        painter.drawEllipse(0, 0, self.width(), self.height())


class NavButton(QPushButton):
    def __init__(self, text, glow_hex, orbitron_family, parent=None):
        super().__init__(text, parent)
        self.glow_color = QColor(glow_hex)
        self.is_active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(48)
        self.setFont(QFont(orbitron_family, 10, QFont.Weight.Bold))

        self._base_style = f"""
            QPushButton {{
                background-color: transparent;
                color: {CLOUDY_SKY};
                border: 2px solid {PERSIAN_BLUE};
                border-radius: 12px;
                padding: 12px 15px 12px 22px;
                font-size: 12px;
                letter-spacing: 1.5px;
                text-align: left;
            }}
            QPushButton:hover {{
                border: 2px solid {self.glow_color.name()};
            }}
            QPushButton:pressed {{
                background-color: rgba(255,255,255,10);
            }}
        """
        self._active_style = f"""
            QPushButton {{
                background-color: {self.glow_color.name()};
                color: #ffffff;
                border: 2px solid {self.glow_color.name()};
                border-radius: 12px;
                padding: 12px 15px 12px 22px;
                font-size: 12px;
                letter-spacing: 1.5px;
                text-align: left;
            }}
        """
        self.setStyleSheet(self._base_style)

    def set_active(self, active: bool):
        self.is_active = active
        self.setStyleSheet(self._active_style if active else self._base_style)


class StatCard(QFrame):
    def __init__(self, title, value, accent_hex, orbitron, mono, subtitle=""):
        super().__init__()
        self.setObjectName("StatCard")
        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(6)

        title_lbl = QLabel(title.upper())
        title_lbl.setFont(QFont(orbitron, 9, QFont.Weight.Bold))
        title_lbl.setStyleSheet(
            f"color: {TEXT_MUTED}; letter-spacing: 2px; border: none; background: transparent;"
        )

        self.value_lbl = QLabel(value)
        self.value_lbl.setFont(QFont(orbitron, 22, QFont.Weight.Black))
        self.value_lbl.setStyleSheet(f"color: {accent_hex}; border: none; background: transparent;")

        layout.addWidget(title_lbl)
        layout.addWidget(self.value_lbl)

        if subtitle:
            self.sub_lbl = QLabel(subtitle)
            self.sub_lbl.setFont(QFont(mono, 9))
            self.sub_lbl.setStyleSheet(f"color: {TEXT_MUTED}; border: none; background: transparent;")
            layout.addWidget(self.sub_lbl)

        layout.addStretch()

        self.setStyleSheet(f"""
            QFrame#StatCard {{
                background-color: {BG_CARD};
                border: 1px solid {TRUE_AZURE};
                border-radius: 16px;
            }}
        """)
        apply_card_shadow(self)

    def update_value(self, new_val):
        self.value_lbl.setText(str(new_val))

    def update_subtitle(self, new_sub):
        if hasattr(self, "sub_lbl"):
            self.sub_lbl.setText(str(new_sub))


class GridBackground(QWidget):
    """Faint 40px grid painted behind the stacked pages."""

    def paintEvent(self, event):
        painter = QPainter(self)
        line_color = QColor("#cbd5e1")  # slate-300 — reads as a grid line on light bg, not a color wash
        line_color.setAlpha(140)
        painter.setPen(line_color)
        step = 40
        for x in range(0, self.width(), step):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), step):
            painter.drawLine(0, y, self.width(), y)


class ScanlineOverlay(QWidget):
    """CRT-style horizontal lines, click-through, sits on top of the whole window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        color = QColor(0, 0, 0, 15)
        painter.setPen(color)
        for y in range(0, self.height(), 4):
            painter.drawLine(0, y, self.width(), y)


def make_tool_button(text: str, color: str, orbitron_family: str) -> QPushButton:
    """Small outlined icon+label button used in the template builder's toolbelt.
    Factored out so any future page needing the same look doesn't recreate it."""
    btn = QPushButton(text)
    btn.setFont(QFont(orbitron_family, 9, QFont.Weight.Bold))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {BG_PANEL}; color: {color};
            border: 2px solid {color}; border-radius: 6px; padding: 10px;
        }}
        QPushButton:hover {{ background-color: {color}; color: #000000; }}
    """)
    return btn

from PySide6.QtWidgets import QGraphicsDropShadowEffect
from PySide6.QtGui import QColor


def apply_card_shadow(widget, color="#94a3b8", blur=24, y_offset=6, alpha=90):
    """
    Gives any card/button/panel the soft colored-shadow 'elevation' look
    common in React/Angular dashboards (Tailwind's shadow-lg, MUI Paper,
    etc). Call once after constructing the widget:

        card = QFrame()
        apply_card_shadow(card)

    Kept as a standalone function (not baked into StatCard.__init__ etc.)
    so any future widget — a card, a button, a dialog panel — can opt in
    without inheriting from a shared base class.
    """
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setXOffset(0)
    shadow.setYOffset(y_offset)
    qcolor = QColor(color)
    qcolor.setAlpha(alpha)
    shadow.setColor(qcolor)
    widget.setGraphicsEffect(shadow)
