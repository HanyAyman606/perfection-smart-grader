"""
pages/setup_page.py
--------------------
"Exam Blueprint Configuration" page: exam mode (registry-driven), MCQ
count + per-range mark values, and essay config — saved into the active
workspace via ProjectManager.
"""

from PySide6.QtWidgets import (
    QLabel, QGridLayout, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QScrollArea, QWidget, QPushButton, QMessageBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import INPUT_STYLE, CLOUDY_SKY, NEON_PINK, TEXT_MUTED, BG_PANEL
from admin_dashboard.pages.base import build_page_shell
from admin_dashboard.exam_modes import EXAM_MODES, DEFAULT_MODE_ID
from admin_dashboard.widgets.mcq_range_builder import MCQRangeBuilder


class SetupPage(QWidget):
    def __init__(self, fonts, project_manager):
        super().__init__()
        self.fonts = fonts
        self.project_manager = project_manager
        self.essay_spinboxes = []

        content_layout = build_page_shell(
            self, "Exam Blueprint Configuration", CLOUDY_SKY, fonts.orbitron
        )
        self._build_ui(content_layout)

    def _build_ui(self, content_layout):
        orbitron = self.fonts.orbitron

        # -- Exam mode + MCQ count -----------------------------------
        top_grid = QGridLayout()
        top_grid.setSpacing(20)

        self.exam_mode_combo = QComboBox()
        for mode in EXAM_MODES:
            self.exam_mode_combo.addItem(mode.label, userData=mode.id)
        self.exam_mode_combo.setCurrentIndex(
            next(i for i, m in enumerate(EXAM_MODES) if m.id == DEFAULT_MODE_ID)
        )
        self.exam_mode_combo.setStyleSheet(INPUT_STYLE)

        self.mcq_count_spin = QSpinBox()
        self.mcq_count_spin.setRange(1, 150)
        self.mcq_count_spin.setValue(10)
        self.mcq_count_spin.setStyleSheet(INPUT_STYLE)
        self.mcq_count_spin.valueChanged.connect(self._on_mcq_count_changed)

        top_grid.addWidget(self._make_label("EXAM MODE:"), 0, 0)
        top_grid.addWidget(self.exam_mode_combo, 0, 1)
        top_grid.addWidget(self._make_label("TOTAL MCQ QUESTIONS:"), 1, 0)
        top_grid.addWidget(self.mcq_count_spin, 1, 1)
        content_layout.addLayout(top_grid)

        # -- MCQ mark ranges ------------------------------------------
        ranges_lbl = self._make_label("MARK RANGES:")
        content_layout.addWidget(ranges_lbl)

        self.range_builder = MCQRangeBuilder(orbitron, self.fonts.mono)
        self.range_builder.set_total_questions(self.mcq_count_spin.value())
        content_layout.addWidget(self.range_builder)

        # -- Essay config -----------------------------------------------
        self.essay_checkbox = QCheckBox(" INCLUDE WRITTEN / ESSAY QUESTIONS")
        self.essay_checkbox.setFont(QFont(orbitron, 11, QFont.Weight.Bold))
        self.essay_checkbox.setStyleSheet(f"color: {NEON_PINK};")
        self.essay_checkbox.setChecked(False)  # off by default, per spec

        essay_row = QGridLayout()
        essay_row.addWidget(self.essay_checkbox, 0, 0, 1, 2)

        self.essay_count_spin = QSpinBox()
        self.essay_count_spin.setRange(1, 20)
        self.essay_count_spin.setStyleSheet(INPUT_STYLE)
        self.essay_count_spin.setEnabled(False)
        self.essay_count_spin.valueChanged.connect(self.generate_essay_inputs)

        self.essay_count_lbl = self._make_label("ESSAY COUNT:")
        essay_row.addWidget(self.essay_count_lbl, 0, 2)
        essay_row.addWidget(self.essay_count_spin, 0, 3)

        self.essay_count_lbl.setVisible(False)
        self.essay_count_spin.setVisible(False)

        content_layout.addLayout(essay_row)

        # -- Essay per-question point inputs (generated dynamically) ------
        self.essay_scroll = QScrollArea()
        self.essay_scroll.setWidgetResizable(True)
        self.essay_scroll.setStyleSheet("border: none; background: transparent;")
        self.essay_container = QWidget()
        self.essay_container.setStyleSheet("background: transparent;")
        self.essay_layout = QGridLayout(self.essay_container)
        self.essay_scroll.setWidget(self.essay_container)
        self.essay_scroll.setVisible(False)
        content_layout.addWidget(self.essay_scroll)

        self.essay_checkbox.toggled.connect(self.toggle_essay_inputs)


        content_layout.addStretch()

        # -- Grand total summary ------------------------------------------
        self.total_summary_lbl = QLabel("")
        self.total_summary_lbl.setFont(QFont(orbitron, 12, QFont.Weight.Bold))
        self.total_summary_lbl.setStyleSheet(f"color: {NEON_PINK};")
        content_layout.addWidget(self.total_summary_lbl)

        self.range_builder.changed.connect(self._refresh_grand_total)
        self._refresh_grand_total()

        # -- Save ---------------------------------------------------------
        btn_save_blueprint = QPushButton("💾 SAVE EXAM BLUEPRINT")
        btn_save_blueprint.setFont(QFont(orbitron, 12, QFont.Weight.Bold))
        btn_save_blueprint.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_blueprint.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {CLOUDY_SKY};
                border: 2px solid {CLOUDY_SKY}; border-radius: 8px; padding: 15px; margin-top: 10px;
            }}
            QPushButton:hover {{ background-color: {CLOUDY_SKY}; color: #ffffff; }}
        """)
        btn_save_blueprint.clicked.connect(self.save_blueprint)
        content_layout.addWidget(btn_save_blueprint)


    def _make_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(QFont(self.fonts.orbitron, 10, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        return lbl

    # ------------------------------------------------------------------
    def _on_mcq_count_changed(self, value):
        self.range_builder.set_total_questions(value)

    def toggle_essay_inputs(self, checked):
        self.essay_count_lbl.setVisible(checked)
        self.essay_count_spin.setVisible(checked)
        self.essay_count_spin.setEnabled(checked)
        self.essay_scroll.setVisible(checked)
        if checked:
            self.generate_essay_inputs()
        else:
            self.clear_essay_inputs()
        self._refresh_grand_total()
    def clear_essay_inputs(self):
        for i in reversed(range(self.essay_layout.count())):
            widget = self.essay_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        self.essay_spinboxes.clear()

    def generate_essay_inputs(self):
        if not self.essay_checkbox.isChecked():
            return
        self.clear_essay_inputs()

        orbitron = self.fonts.orbitron
        count = self.essay_count_spin.value()
        for i in range(count):
            lbl = self._make_label(f"Essay Q{i + 1} Points:")

            spin = QDoubleSpinBox()
            spin.setRange(0.5, 50.0)
            spin.setValue(2.0)
            spin.setStyleSheet(INPUT_STYLE)

            row = i // 3
            col = (i % 3) * 2
            self.essay_layout.addWidget(lbl, row, col)
            self.essay_layout.addWidget(spin, row, col + 1)
            self.essay_spinboxes.append(spin)
            spin.valueChanged.connect(self._refresh_grand_total)

        self._refresh_grand_total()

    def save_blueprint(self):
        if not self.project_manager.is_active:
            return

        if not self.range_builder.is_valid():
            QMessageBox.warning(
                self, "Incomplete Mark Ranges",
                "Every MCQ question must be covered by a mark range before saving."
            )
            return

        has_essays = self.essay_checkbox.isChecked()
        essay_data = {}
        if has_essays:
            for i, spin in enumerate(self.essay_spinboxes):
                essay_data[f"Q{i+1}"] = spin.value()

        mode_id = self.exam_mode_combo.currentData()

        self.project_manager.save_blueprint(
            mode_id=mode_id,
            mcq_count=self.mcq_count_spin.value(),
            mcq_ranges=self.range_builder.get_ranges(),
            has_essays=has_essays,
            essay_points_map=essay_data,
        )

        QMessageBox.information(
            self, "Success",
            f"Blueprint saved to project workspace:\n{self.project_manager.project_name}"
        )

    def load_blueprint(self):
        """Called by the dashboard right after a workspace is activated,
        so a previously-saved blueprint repopulates every field instead of
        resetting to this page's built-in defaults."""
        if not self.project_manager.is_active:
            return

        config = self.project_manager.load_config()

        mode_id = config.get("mode", DEFAULT_MODE_ID)
        idx = next((i for i, m in enumerate(EXAM_MODES) if m.id == mode_id), 0)
        self.exam_mode_combo.setCurrentIndex(idx)

        mcq_count = config.get("mcq_count", 10)
        self.mcq_count_spin.blockSignals(True)
        self.mcq_count_spin.setValue(mcq_count)
        self.mcq_count_spin.blockSignals(False)

        saved_ranges = config.get("mcq_ranges", [])
        if saved_ranges:
            self.range_builder.load_ranges(mcq_count, saved_ranges)
        else:
            self.range_builder.set_total_questions(mcq_count)

        has_essays = config.get("has_essays", False)
        essay_map = config.get("essay_points_map", {})

        self.essay_checkbox.blockSignals(True)
        self.essay_checkbox.setChecked(has_essays)
        self.essay_checkbox.blockSignals(False)
        self.essay_count_lbl.setVisible(has_essays)
        self.essay_count_spin.setVisible(has_essays)
        self.essay_count_spin.setEnabled(has_essays)
        self.essay_scroll.setVisible(has_essays)

        if has_essays and essay_map:
            self.essay_count_spin.blockSignals(True)
            self.essay_count_spin.setValue(len(essay_map))
            self.essay_count_spin.blockSignals(False)
            self.generate_essay_inputs()
            for spin, points in zip(self.essay_spinboxes, essay_map.values()):
                spin.setValue(points)
        else:
            self.clear_essay_inputs()
        self._refresh_grand_total()

    def _refresh_grand_total(self):
        mcq_total = self.range_builder.total_points()
        essay_total = sum(spin.value() for spin in self.essay_spinboxes) if self.essay_checkbox.isChecked() else 0.0
        grand_total = mcq_total + essay_total
        self.total_summary_lbl.setText(f"EXAM TOTAL: {grand_total:g} pts  (MCQ {mcq_total:g} + Essay {essay_total:g})")