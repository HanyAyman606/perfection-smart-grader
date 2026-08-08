"""
widgets/qr_code.py
-------------------
Renders arbitrary text (the LAN IP, in practice) as a QR code QPixmap.

Uses the `qrcode` package purely for its module matrix (qr.get_matrix()),
then paints the squares directly with QPainter — this avoids pulling in
Pillow just to rasterize an image we're about to hand straight to Qt
anyway. Keeping it a small standalone helper (rather than inlining it in
dashboard.py) means the sidebar and any other "scan to connect" spot can
reuse it later without re-deriving the box-drawing math.
"""

import qrcode
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QPainter, QColor


def generate_qr_pixmap(data: str, box_size: int = 4, border: int = 2,
                        fg: str = "#0d1117", bg: str = "#ffffff") -> QPixmap:
    """Returns a QPixmap of `data` encoded as a QR code. `box_size` is the
    pixel size of one QR module (square); `border` is the quiet-zone width
    in modules, same units qrcode itself uses."""
    qr = qrcode.QRCode(border=border, box_size=box_size)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()

    modules_per_side = len(matrix)
    side_px = modules_per_side * box_size

    pixmap = QPixmap(side_px, side_px)
    pixmap.fill(QColor(bg))

    painter = QPainter(pixmap)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(fg))
    for row_idx, row in enumerate(matrix):
        for col_idx, is_dark in enumerate(row):
            if is_dark:
                painter.drawRect(col_idx * box_size, row_idx * box_size, box_size, box_size)
    painter.end()

    return pixmap