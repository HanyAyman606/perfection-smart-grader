"""
pages/base.py
-------------
Every content page (Setup, Templates, Session sub-pages) starts with the
same heading + optional badge header. Factored out once instead of being
copy-pasted per page.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtGui import QFont


def build_page_shell(target_widget, heading: str, accent_hex: str, orbitron_family: str, extra_header=None):
    """
    Builds the header (title + optional badge) directly onto target_widget
    and returns its content QVBoxLayout so real widgets can be wired in
    below the header, e.g.:

        content_layout = build_page_shell(self, "My Page", ACCENT, orbitron)
        content_layout.addWidget(my_widget)

    target_widget is typically the QWidget/QWidget-subclass page itself
    (e.g. `self` inside a page class) — this avoids creating a throwaway
    QWidget and re-parenting its layout, which Qt does not support cleanly.
    """
    target_widget.setStyleSheet("background: transparent;")
    layout = QVBoxLayout(target_widget)
    layout.setContentsMargins(36, 30, 36, 30)
    layout.setSpacing(22)

    header_row = QHBoxLayout()
    heading_lbl = QLabel(heading)
    heading_lbl.setFont(QFont(orbitron_family, 20, QFont.Weight.Black))
    heading_lbl.setStyleSheet(
        f"color: {accent_hex}; letter-spacing: 1px; background: transparent; border: none;"
    )
    header_row.addWidget(heading_lbl)
    header_row.addStretch()
    if extra_header:
        header_row.addWidget(extra_header)
    layout.addLayout(header_row)

    return layout
