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
    background: str = BG_PANEL,
    extra_style: str = "",
) -> QPushButton:
    """The 'outline that fills solid on hover' button used for every
    secondary/cancel/browse action across dialogs and pages.

    extra_style: raw CSS declarations (e.g. "margin-top: 10px;") appended
    inside the QPushButton{} block for the rare one-off tweak that isn't
    worth a new named parameter."""
    btn = QPushButton(text)
    btn.setFont(QFont(font_family, font_size, QFont.Weight.Bold))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {background}; color: {accent};
            border: 2px solid {accent}; border-radius: {radius}px; padding: {padding};
            {extra_style}
        }}
        QPushButton:hover {{ background-color: {accent}; color: {hover_text_color}; }}
        QPushButton:disabled {{ background-color: {background}; color: {TEXT_MUTED}; border-color: {TEXT_MUTED}; }}
    """)
    return btn


def make_dashed_button(
    text: str,
    font_family: str,
    accent: str,
    *,
    font_size: int = 10,
    padding: str = "12px",
    radius: int = 8,
    background: str = "transparent",
) -> QPushButton:
    """The 'dashed outline that solidifies on hover' button used for
    every '+ ADD ...' affordance (add group, add mark range, browse for
    another folder). Same interaction idea as make_outline_button but
    dashed at rest, signaling 'this creates something new' rather than
    'this acts on something that already exists'."""
    btn = QPushButton(text)
    btn.setFont(QFont(font_family, font_size, QFont.Weight.Bold))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {background}; color: {accent};
            border: 2px dashed {accent}; border-radius: {radius}px; padding: {padding};
        }}
        QPushButton:hover {{ background-color: {accent}; color: #ffffff; border-style: solid; }}
    """)
    return btn


def make_icon_button(
    glyph: str,
    accent_hover: str,
    *,
    size: int = 34,
    font_size: int = 16,
) -> QPushButton:
    """Small borderless glyph-only button (✎ rename, ✕ delete) used on
    list/card rows. Neutral at rest, colors on hover to hint at the
    action (e.g. red for delete)."""
    btn = QPushButton(glyph)
    btn.setFixedSize(size, size)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; "
        f"border: none; font-weight: bold; font-size: {font_size}px; padding: 0px; text-align: center; }} "
        f"QPushButton:hover {{ color: {accent_hover}; }}"
    )
    return btn


def make_link_button(
    text: str,
    *,
    hover_color: str = "#ffffff",
    padding: str = "8px",
) -> QPushButton:
    """Borderless, transparent 'CANCEL'/'CLOSE' link-style button used at
    the bottom of dialogs as the low-emphasis dismiss action, next to a
    make_outline_button primary action."""
    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; border: none; padding: {padding}; }} "
        f"QPushButton:hover {{ color: {hover_color}; }}"
    )
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