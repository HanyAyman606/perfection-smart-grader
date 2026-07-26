"""
pages/templates_page.py
------------------------
"ROI Template Builder" page: load an exam image, draw the ROI bounding
box, crop + save it into the active workspace via ProjectManager.
"""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QMessageBox, QFileDialog
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QFont, QImageReader, QPixmap

from admin_dashboard.theme import (
    CLOUDY_SKY, TEXT_MUTED, SKY_AQUA, ELECTRIC_SAPPHIRE, NEON_PINK, WARN_COLOR,
    RASPBERRY_PLUM, BG_PANEL
)
from admin_dashboard.pages.base import build_page_shell
from admin_dashboard.widgets.common import make_tool_button
from admin_dashboard.widgets.roi_canvas import ROIGraphicsView


class TemplatesPage(QWidget):
    def __init__(self, fonts, project_manager):
        super().__init__()
        self.fonts = fonts
        self.project_manager = project_manager

        content_layout = build_page_shell(self, "ROI Template Builder", RASPBERRY_PLUM, fonts.orbitron)
        self._build_ui(content_layout)

    def _build_ui(self, content_layout):
        orbitron = self.fonts.orbitron

        tool_row = QHBoxLayout()
        tool_row.setSpacing(10)

        btn_load = make_tool_button("📷 1. LOAD IMAGE", CLOUDY_SKY, orbitron)
        btn_pan = make_tool_button("🖐 PAN MODE", TEXT_MUTED, orbitron)
        btn_rotate = make_tool_button("↻ ROTATE", SKY_AQUA, orbitron)
        btn_zoom_in = make_tool_button("➕ ZOOM", ELECTRIC_SAPPHIRE, orbitron)
        btn_zoom_out = make_tool_button("➖ ZOOM", ELECTRIC_SAPPHIRE, orbitron)
        btn_draw = make_tool_button("🎯 2. DRAW ROI", NEON_PINK, orbitron)
        btn_clear_roi = make_tool_button("↺ CLEAR ROI", WARN_COLOR, orbitron)

        self.roi_canvas = ROIGraphicsView()
        self.roi_canvas.setMinimumHeight(420)

        btn_load.clicked.connect(self.load_template_image)
        btn_pan.clicked.connect(lambda: self.roi_canvas.set_mode("PAN"))
        btn_rotate.clicked.connect(self.roi_canvas.rotate_image)
        btn_zoom_in.clicked.connect(self.roi_canvas.zoom_in)
        btn_zoom_out.clicked.connect(self.roi_canvas.zoom_out)
        btn_draw.clicked.connect(lambda: self.roi_canvas.set_mode("DRAW"))
        btn_clear_roi.clicked.connect(self.roi_canvas.clear_roi)

        for b in (btn_load, btn_pan, btn_rotate, btn_zoom_in, btn_zoom_out, btn_draw, btn_clear_roi):
            tool_row.addWidget(b)
        tool_row.addStretch()

        self.btn_save_tpl = QPushButton("💾 3. CROP & SAVE TEMPLATE")
        self.btn_save_tpl.setFont(QFont(orbitron, 12, QFont.Weight.Bold))
        self.btn_save_tpl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_tpl.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {NEON_PINK};
                border: 2px solid {NEON_PINK}; border-radius: 8px; padding: 15px; margin-top: 10px;
            }}
            QPushButton:hover {{ background-color: {NEON_PINK}; color: #ffffff; }}
        """)
        self.btn_save_tpl.clicked.connect(self.save_template)

        content_layout.addLayout(tool_row)
        content_layout.addWidget(self.roi_canvas)
        content_layout.addWidget(self.btn_save_tpl)

    def load_template_image(self):
        options = QFileDialog.Option.DontUseNativeDialog
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Exam Image", "", "Images (*.png *.jpg *.jpeg)", options=options
        )
        if file_path:
            # Auto-correct camera EXIF orientation so photos load upright
            reader = QImageReader(file_path)
            reader.setAutoTransform(True)
            image = reader.read()
            pixmap = QPixmap.fromImage(image)
            self.roi_canvas.load_image(pixmap)

    def save_template(self):
        if not self.project_manager.is_active:
            QMessageBox.warning(self, "Error", "No active workspace. Open or create a project first.")
            return

        roi_coords = self.roi_canvas.get_roi_coordinates()
        if not roi_coords:
            QMessageBox.warning(self, "Error", "You must draw a target ROI box first.")
            return

        try:
            # Crop the CURRENT pixmap (rotation baked in) so the crop always
            # matches what was seen on screen.
            saved_image_path = self.project_manager.template_save_path()
            crop_rect = QRect(roi_coords["x"], roi_coords["y"], roi_coords["w"], roi_coords["h"])
            cropped_pixmap = self.roi_canvas.current_pixmap.copy(crop_rect)
            cropped_pixmap.save(saved_image_path, "JPG", 100)

            self.project_manager.save_template(saved_image_path, roi_coords)

            QMessageBox.information(self, "Success", "Template cropped and saved to workspace!")

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
