"""
widgets/styled.py
------------------
Factory functions for the two button/label patterns that were being
hand-rolled with a fresh QFont(...) + setStyleSheet(f\"\"\"...\"\"\") block in
nearly every screen/dialog/page (40+ occurrences across the codebase).

Rationale: theme.py is already the single source of truth for *colors*,
but not for *how those colors get applied to a widget* — every call site
independently re-derived "outline button that fills on hover" or "bold
letter-spaced title label" from raw QSS. That meant a shared visual tweak
(e.g. border-radius, hover behavior) required editing every file instead
of one. This module is that missing single source of truth for widget
*style*, the same way theme.py is for palette.

Usage — replaces this (repeated verbatim everywhere):
    btn = QPushButton("BROWSE")
    btn.setFont(QFont(orbitron, 10, QFont.Weight.Bold))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(f'''
        QPushButton {{ background-color: {BG_PANEL}; color: {CLOUDY_SKY};
        border: 2px solid {CLOUDY_SKY}; border-radius: 8px; padding: 10px 14px; }}
        QPushButton:hover {{ background-color: {CLOUDY_SKY}; color: #000000; }}
    ''')

...with this:
    btn = make_outline_button("BROWSE", orbitron, accent=CLOUDY_SKY)

New call sites should use this module. Existing call sites can be migrated
incrementally — nothing here changes behavior, only where the styling
rule lives.
"""

from PySide6.QtWidgets import QPushButton, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import BG_PANEL, TEXT_MUTED


def make_outline_button(
    text: str,
    font_family: str,
    accent: str,
    *,
    font_size: int = 10,
    hover_text_color: str = "#ffffff",
    padding: str = "12px",
    radius: int = 8,
) -> QPushButton:
    """The 'outline that fills solid on hover' button used for every
    secondary/cancel/browse action across dialogs and pages."""
    btn = QPushButton(text)
    btn.setFont(QFont(font_family, font_size, QFont.Weight.Bold))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {BG_PANEL}; color: {accent};
            border: 2px solid {accent}; border-radius: {radius}px; padding: {padding};
        }}
        QPushButton:hover {{ background-color: {accent}; color: {hover_text_color}; }}
        QPushButton:disabled {{ background-color: {BG_PANEL}; color: {TEXT_MUTED}; border-color: {TEXT_MUTED}; }}
    """)
    return btn


def make_title_label(
    text: str,
    font_family: str,
    color: str,
    *,
    font_size: int = 14,
    letter_spacing: str = "1px",
    weight: QFont.Weight = QFont.Weight.Black,
    center: bool = True,
) -> QLabel:
    """The bold, letter-spaced, colored heading label used at the top of
    nearly every dialog/card ('CREATE NEW WORKSPACE', 'SAVE LOCATION:', ...)."""
    lbl = QLabel(text)
    lbl.setFont(QFont(font_family, font_size, weight))
    lbl.setStyleSheet(f"color: {color}; letter-spacing: {letter_spacing};")
    if center:
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl


def make_muted_label(text: str, *, font_size: int = 10) -> QLabel:
    """Small secondary/caption text, e.g. field labels like 'SAVE LOCATION:'."""
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color:{TEXT_MUTED}; font-size:{font_size}px;")
    return lbl