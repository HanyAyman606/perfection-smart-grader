"""
widgets/roi_canvas.py
----------------------
Precision exam-sheet canvas used by the Template Builder page.
- PAN mode by default (drag to move, no accidental drawing). Explicit
  buttons drive zoom/rotate; mouse wheel is disabled so it never fights
  touchpad scrolling.
- DRAW mode is entered on demand; finishing a box auto-reverts to PAN.
- Rotation is baked into the pixmap (not just view-transformed) so a
  cropped save always matches what's on screen.
- SmoothPixmapTransform render hint keeps zoomed-in image quality crisp.
"""

from PySide6.QtWidgets import QGraphicsView, QGraphicsScene
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QTransform

from admin_dashboard.theme import BG_CARD, TRUE_AZURE, PERSIAN_BLUE, NEON_PINK, BG_PANEL


class ROIGraphicsView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        self.setStyleSheet(f"""
            QGraphicsView {{
                background-color: {BG_CARD};
                border: 2px solid {TRUE_AZURE};
                border-radius: 12px;
            }}
            QScrollBar:horizontal {{
                border: none; background: {BG_PANEL}; height: 14px; margin: 0px;
            }}
            QScrollBar::handle:horizontal {{
                background: {PERSIAN_BLUE}; min-width: 20px; border-radius: 7px;
            }}
            QScrollBar:vertical {{
                border: none; background: {BG_PANEL}; width: 14px; margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {PERSIAN_BLUE}; min-height: 20px; border-radius: 7px;
            }}
        """)

        self.original_pixmap = None   # as-loaded (EXIF-corrected), rotation = 0
        self.current_pixmap = None    # original with rotation baked in
        self.image_item = None
        self.roi_rect = None
        self.start_pos = None
        self.rotation = 0
        self.current_mode = "PAN"
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    # -- mode -----------------------------------------------------------
    def set_mode(self, mode):
        self.current_mode = mode
        self.setDragMode(
            QGraphicsView.DragMode.NoDrag if mode == "DRAW" else QGraphicsView.DragMode.ScrollHandDrag
        )

    # -- loading / orientation -------------------------------------------
    def load_image(self, pixmap):
        self.original_pixmap = pixmap
        self.rotation = 0
        self._apply_pixmap(pixmap)
        self.set_mode("PAN")

    def _apply_pixmap(self, pixmap):
        self.current_pixmap = pixmap
        self.scene.clear()
        self.image_item = self.scene.addPixmap(pixmap)
        self.image_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.roi_rect = None
        self.start_pos = None
        self.setSceneRect(self.image_item.boundingRect())
        self.resetTransform()
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def rotate_image(self):
        """Bakes a 90° clockwise rotation into the pixmap so crop coords stay valid."""
        if self.original_pixmap is None:
            return
        self.rotation = (self.rotation + 90) % 360
        transform = QTransform().rotate(self.rotation)
        rotated = self.original_pixmap.transformed(transform, Qt.TransformationMode.SmoothTransformation)
        self._apply_pixmap(rotated)
        self.set_mode("PAN")

    # -- zoom (buttons only — wheel is disabled below) --------------------
    def zoom_in(self):
        self.scale(1.2, 1.2)

    def zoom_out(self):
        self.scale(1.0 / 1.2, 1.0 / 1.2)

    def wheelEvent(self, event):
        pass  # mouse/touchpad zoom intentionally disabled — use the buttons

    # -- ROI drawing --------------------------------------------------------
    def mousePressEvent(self, event):
        if self.current_mode == "DRAW" and self.image_item:
            self.start_pos = self.mapToScene(event.position().toPoint())
            if self.roi_rect:
                self.scene.removeItem(self.roi_rect)
            pen = QPen(QColor(NEON_PINK))
            pen.setWidth(3)
            pen.setCosmetic(True)  # constant on-screen thickness regardless of zoom
            brush = QBrush(QColor(247, 37, 133, 40))
            self.roi_rect = self.scene.addRect(QRectF(self.start_pos, self.start_pos), pen, brush)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.current_mode == "DRAW" and self.start_pos and self.roi_rect:
            current_pos = self.mapToScene(event.position().toPoint())
            rect = QRectF(self.start_pos, current_pos).normalized()
            self.roi_rect.setRect(rect)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.current_mode == "DRAW":
            self.start_pos = None
            self.set_mode("PAN")  # auto-revert so a second click can't redraw by accident
        else:
            super().mouseReleaseEvent(event)

    def clear_roi(self):
        """Erases the current bounding box without removing the image."""
        if self.roi_rect:
            self.scene.removeItem(self.roi_rect)
            self.roi_rect = None

    def get_roi_coordinates(self):
        if not self.roi_rect:
            return None
        rect = self.roi_rect.rect()
        return {
            "x": int(rect.x()),
            "y": int(rect.y()),
            "w": int(rect.width()),
            "h": int(rect.height()),
        }
