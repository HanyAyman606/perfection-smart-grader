"""
pages/templates_page.py
------------------------
"ROI Template Builder" page: load an exam image, draw the ROI bounding
box, crop + save it into the active workspace via ProjectManager.
"""

import os

from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QMessageBox, QFileDialog, QLabel
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QFont, QImageReader, QPixmap

from admin_dashboard.theme import (
    CLOUDY_SKY, TEXT_MUTED, SKY_AQUA, ELECTRIC_SAPPHIRE, NEON_PINK, WARN_COLOR,
    RASPBERRY_PLUM, BG_PANEL
)
from admin_dashboard.pages.base import build_page_shell
from admin_dashboard.widgets.common import make_tool_button
from admin_dashboard.widgets.roi_canvas import ROIGraphicsView
from admin_dashboard.screens.dialogs import show_warning, show_info, show_error, ask_yes_no, open_file_dialog


class TemplatesPage(QWidget):
    def __init__(self, fonts, project_manager):
        super().__init__()
        self.fonts = fonts
        self.project_manager = project_manager

        content_layout = build_page_shell(self, "ROI Template Builder", RASPBERRY_PLUM, fonts.orbitron)
        self._build_ui(content_layout)

    def _build_ui(self, content_layout):
        orbitron = self.fonts.orbitron

        self.status_lbl = QLabel("")
        self.status_lbl.setFont(QFont(self.fonts.mono, 10))
        content_layout.addWidget(self.status_lbl)

        tool_row = QHBoxLayout()
        tool_row.setSpacing(10)

        btn_load = make_tool_button("📷 1. LOAD IMAGE", CLOUDY_SKY, orbitron)
        btn_pan = make_tool_button("🖐 PAN MODE", TEXT_MUTED, orbitron)
        btn_rotate = make_tool_button("↻ ROTATE", SKY_AQUA, orbitron)
        btn_zoom_in = make_tool_button("➕ ZOOM", ELECTRIC_SAPPHIRE, orbitron)
        btn_zoom_out = make_tool_button("➖ ZOOM", ELECTRIC_SAPPHIRE, orbitron)
        btn_draw = make_tool_button("🎯 2. DRAW ROI", NEON_PINK, orbitron)
        btn_clear_roi = make_tool_button("↺ CLEAR ROI", WARN_COLOR, orbitron)
        btn_clear_image = make_tool_button("🗑 CLEAR IMAGE", WARN_COLOR, orbitron)

        self.roi_canvas = ROIGraphicsView()
        self.roi_canvas.setMinimumHeight(420)

        btn_load.clicked.connect(self.load_template_image)
        btn_pan.clicked.connect(lambda: self.roi_canvas.set_mode("PAN"))
        btn_rotate.clicked.connect(self.roi_canvas.rotate_image)
        btn_zoom_in.clicked.connect(self.roi_canvas.zoom_in)
        btn_zoom_out.clicked.connect(self.roi_canvas.zoom_out)
        btn_draw.clicked.connect(lambda: self.roi_canvas.set_mode("DRAW"))
        btn_clear_roi.clicked.connect(self.roi_canvas.clear_roi)
        btn_clear_image.clicked.connect(self.clear_image)

        for b in (btn_load, btn_pan, btn_rotate, btn_zoom_in, btn_zoom_out, btn_draw, btn_clear_roi, btn_clear_image):
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
        file_path = open_file_dialog(self, "Select Exam Image", "Images (*.png *.jpg *.jpeg)")
        if file_path:
            # Auto-correct camera EXIF orientation so photos load upright
            reader = QImageReader(file_path)
            reader.setAutoTransform(True)
            image = reader.read()
            pixmap = QPixmap.fromImage(image)
            self.roi_canvas.load_image(pixmap)
            self.status_lbl.setStyleSheet(f"color: {WARN_COLOR};")
            self.status_lbl.setText("⚠ Image loaded but not saved yet — draw a box and click Save.")

    def save_template(self):
        if not self.project_manager.is_active:
            show_warning(self, self.fonts.orbitron, self.fonts.mono, "Error", "No active workspace. Open or create a project first.")
            return

        if self.roi_canvas.current_pixmap is None:
            show_warning(self, self.fonts.orbitron, self.fonts.mono, "Error", "Load an exam image first.")
            return

        roi_coords = self.roi_canvas.get_roi_coordinates()
        if not roi_coords:
            show_warning(self, self.fonts.orbitron, self.fonts.mono, "Error", "You must draw a target ROI box first.")
            return

        try:
            # Crop the CURRENT pixmap (rotation baked in) so the crop always
            # matches what was seen on screen.
            cropped_path = self.project_manager.template_save_path()
            crop_rect = QRect(roi_coords["x"], roi_coords["y"], roi_coords["w"], roi_coords["h"])
            cropped_pixmap = self.roi_canvas.current_pixmap.copy(crop_rect)
            cropped_pixmap.save(cropped_path, "JPG", 100)

            # Also persist the full (uncropped) image the box was drawn on —
            # needed so re-opening this workspace can restore the picture
            # and redraw the box on top of it, not just the crop result.
            source_path = self.project_manager.source_template_save_path()
            self.roi_canvas.current_pixmap.save(source_path, "JPG", 100)

            self.project_manager.save_template(cropped_path, roi_coords, source_path)

            self.status_lbl.setStyleSheet(f"color: {CLOUDY_SKY};")
            self.status_lbl.setText("✔ Template saved to workspace.")
            show_info(self, self.fonts.orbitron, self.fonts.mono, "Success", "Template cropped and saved to workspace!")

        except Exception as e:
            show_error(self, self.fonts.orbitron, self.fonts.mono, "Error", str(e))

    # ------------------------------------------------------------------
    def load_saved_template(self):
        """Restores a previously-saved image + ROI box. Called by the
        dashboard right after a workspace is activated, mirroring
        SetupPage.load_blueprint() / ModelAnswerPage.build_rows()."""
        self.roi_canvas.clear_image()

        if not self.project_manager.is_active:
            self.status_lbl.setText("No active workspace.")
            return

        config = self.project_manager.load_config()
        source_path = config.get("source_template_path")
        roi_coords = config.get("roi_coordinates")

        if not source_path or not os.path.exists(source_path):
            self.status_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
            self.status_lbl.setText("No template saved yet for this workspace.")
            return

        pixmap = QPixmap(source_path)
        self.roi_canvas.load_image(pixmap)
        if roi_coords:
            self.roi_canvas.set_roi_from_coordinates(roi_coords)

        self.status_lbl.setStyleSheet(f"color: {CLOUDY_SKY};")
        self.status_lbl.setText("✔ Template loaded from workspace.")

    def clear_image(self):
        """Wipes the canvas AND the saved template files/config — distinct
        from 'Clear ROI', which only erases the box and keeps the image."""
        if not self.project_manager.is_active:
            self.roi_canvas.clear_image()
            self.status_lbl.setText("")
            return

        confirmed = ask_yes_no(self, self.fonts.orbitron, self.fonts.mono, "Clear Image",  "This removes the loaded image and ROI box, and deletes the saved "
        "template files from this workspace. Continue?")

        if not confirmed: return

        self.roi_canvas.clear_image()
        self.project_manager.clear_template()
        self.status_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        self.status_lbl.setText("Image cleared. No template saved for this workspace.")