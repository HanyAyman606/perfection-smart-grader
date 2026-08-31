"""
pages/setup_page.py
--------------------
"Exam Blueprint Configuration" page: exam mode (registry-driven), MCQ
count + per-range mark values, and essay config — saved into the active
workspace via ProjectManager.
"""

from PySide6.QtWidgets import (
    QLabel, QGridLayout, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QScrollArea, QWidget, QPushButton, QMessageBox, QLineEdit, QVBoxLayout
)
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtCore import QRegularExpression
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import INPUT_STYLE, CLOUDY_SKY, NEON_PINK, TEXT_MUTED, BG_PANEL
from admin_dashboard.pages.base import build_page_shell
from admin_dashboard.exam_modes import EXAM_MODES, DEFAULT_MODE_ID
from admin_dashboard.widgets.styled import make_outline_button
from admin_dashboard.widgets.mcq_range_builder import MCQRangeBuilder
from admin_dashboard.screens.dialogs import show_warning, show_info


class SetupPage(QWidget):
    DEFAULT_LETTERS = "CDEFMW"

    # Shamel mode's fixed exam shape — spawned the moment the admin picks
    # "Shamel Mode" from the dropdown, so they don't have to hand-build
    # the same 44-question / 2-essay blueprint every time. Still fully
    # editable afterward; this is just a starting point, not a lock.
    SHAMEL_MCQ_COUNT = 44
    SHAMEL_MCQ_RANGES = [
        {"start": 1, "end": 32, "points": 1.0},
        {"start": 33, "end": 44, "points": 2.0},
    ]
    SHAMEL_ESSAY_COUNT = 2
    SHAMEL_ESSAY_POINTS = 2.0
    SHAMEL_ID_LETTER_COUNT = 7
    SHAMEL_ID_LETTERS = "CDEFMWO"  # DEFAULT_LETTERS + 'O' as the 7th letter

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

        self.main_scroll = QScrollArea()
        self.main_scroll.setWidgetResizable(True)
        self.main_scroll.setStyleSheet("border: none; background: transparent;")
        self.form_container = QWidget()
        self.form_container.setStyleSheet("background: transparent;")
        self.form_layout = QVBoxLayout(self.form_container)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.form_layout.setSpacing(10)
        
        self.main_scroll.setWidget(self.form_container)
        content_layout.addWidget(self.main_scroll)

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
        self.exam_mode_combo.currentIndexChanged.connect(self._on_exam_mode_changed)

        self.mcq_count_spin = QSpinBox()
        self.mcq_count_spin.setRange(1, 150)
        self.mcq_count_spin.setValue(10)
        self.mcq_count_spin.setStyleSheet(INPUT_STYLE)
        self.mcq_count_spin.valueChanged.connect(self._on_mcq_count_changed)

        self.choices_per_question_spin = QSpinBox()
        self.choices_per_question_spin.setRange(2, 8)
        self.choices_per_question_spin.setValue(4)
        self.choices_per_question_spin.setStyleSheet(INPUT_STYLE)
        self.choices_per_question_spin.setEnabled(False)  # locked to 4 — matches Bubble Sheet Studio

        self.id_letter_count_spin = QSpinBox()
        self.id_letter_count_spin.setRange(1, 12)
        self.id_letter_count_spin.setValue(6)
        self.id_letter_count_spin.setStyleSheet(INPUT_STYLE)
        self.id_letter_count_spin.valueChanged.connect(self._on_id_letter_count_changed)

        self.id_letters_edit = QLineEdit(self.DEFAULT_LETTERS)
        self.id_letters_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression("[A-Za-z]*"))
        )
        self.id_letters_edit.setStyleSheet(INPUT_STYLE)

        top_grid.addWidget(self._make_label("EXAM MODE:"), 0, 0)
        top_grid.addWidget(self.exam_mode_combo, 0, 1)
        top_grid.addWidget(self._make_label("TOTAL MCQ QUESTIONS:"), 0, 2)
        top_grid.addWidget(self.mcq_count_spin, 0, 3)
        top_grid.addWidget(self._make_label("CHOICES PER Q:"), 1, 0)
        top_grid.addWidget(self.choices_per_question_spin, 1, 1)
        top_grid.addWidget(self._make_label("ID LETTER COUNT:"), 1, 2)
        top_grid.addWidget(self.id_letter_count_spin, 1, 3)
        top_grid.addWidget(self._make_label("ID LETTERS:"), 2, 0)
        top_grid.addWidget(self.id_letters_edit, 2, 1, 1, 3)
        self.form_layout.addLayout(top_grid)

        # -- MCQ mark ranges ------------------------------------------
        ranges_lbl = self._make_label("MARK RANGES:")
        self.form_layout.addWidget(ranges_lbl)

        self.range_builder = MCQRangeBuilder(orbitron, self.fonts.mono)
        self.range_builder.set_total_questions(self.mcq_count_spin.value())
        self.form_layout.addWidget(self.range_builder)

        # -- Essay config -----------------------------------------------
        self.essay_checkbox = QCheckBox(" INCLUDE WRITTEN / ESSAY QUESTIONS")
        self.essay_checkbox.setFont(QFont(orbitron, 11, QFont.Weight.Bold))
        self.essay_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.essay_checkbox.setStyleSheet(f"""
            QCheckBox {{ color: {NEON_PINK}; spacing: 10px; }}
            QCheckBox::indicator {{
                width: 22px; height: 22px; border: 2px solid {NEON_PINK};
                border-radius: 6px; background-color: {BG_PANEL};
            }}
            QCheckBox::indicator:hover {{ border: 2px solid {CLOUDY_SKY}; }}
            QCheckBox::indicator:checked {{
                background-color: {NEON_PINK}; border: 2px solid {NEON_PINK};
                image: none;
            }}
        """)
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

        self.form_layout.addLayout(essay_row)

        # -- Essay per-question point inputs (generated dynamically) ------
        self.essay_container = QWidget()
        self.essay_container.setStyleSheet("background: transparent;")
        self.essay_layout = QGridLayout(self.essay_container)
        self.essay_container.setVisible(False)
        self.form_layout.addWidget(self.essay_container)

        self.essay_checkbox.toggled.connect(self.toggle_essay_inputs)


        self.form_layout.addStretch()

        # -- Grand total summary ------------------------------------------
        self.total_summary_lbl = QLabel("")
        self.total_summary_lbl.setFont(QFont(orbitron, 12, QFont.Weight.Bold))
        self.total_summary_lbl.setStyleSheet(f"color: {NEON_PINK};")
        self.form_layout.addWidget(self.total_summary_lbl)

        self.range_builder.changed.connect(self._refresh_grand_total)
        self._refresh_grand_total()

        # -- Save ---------------------------------------------------------
        btn_save_blueprint = make_outline_button(
            "💾 SAVE EXAM BLUEPRINT", orbitron, CLOUDY_SKY,
            font_size=12, padding="15px", extra_style="margin-top: 10px;",
        )
        btn_save_blueprint.clicked.connect(self.save_blueprint)
        self.form_layout.addWidget(btn_save_blueprint)


    @staticmethod
    def _default_letters(count: int) -> str:
        """A-B-C... default letter sequence for a freshly-set count."""
        return "".join(chr(ord('A') + (i % 26)) for i in range(count))

    def _on_id_letter_count_changed(self, value: int):
        """Grows/shrinks the letters string to match the new count instead
        of regenerating it, so any manual edits the user made are kept —
        going from 6 to 7 appends one letter, 6 to 5 drops the last one."""
        current = self.id_letters_edit.text()
        diff = value - len(current)
        if diff > 0:
            start_code = ord(current[-1]) + 1 if current else ord('A')
            addition = ""
            code = start_code
            for _ in range(diff):
                if code > ord('Z'):
                    code = ord('A')
                addition += chr(code)
                code += 1
            self.id_letters_edit.setText(current + addition)
        elif diff < 0:
            self.id_letters_edit.setText(current[:value])

    def _make_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(QFont(self.fonts.orbitron, 10, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        return lbl

    # ------------------------------------------------------------------
    def _on_mcq_count_changed(self, value):
        self.range_builder.set_total_questions(value)

    def _on_exam_mode_changed(self, index: int):
        """Fires only on a genuine user pick from the dropdown — never
        during load_blueprint(), which blocks this combo's signals while
        it sets the saved mode. Populating defaults here (rather than
        reacting to config load) is what makes them 'spawn immediately'
        on selection without ever silently overwriting a reopened,
        previously-saved blueprint."""
        mode_id = self.exam_mode_combo.itemData(index)
        if mode_id == "shamel":
            self._apply_shamel_defaults()

    def _apply_shamel_defaults(self):
        """The fixed Shamel-mode shape: 44 MCQs (1-32 worth 1pt, 33-44
        worth 2pts), 2 essay questions worth 2pts each, and a 7-letter
        ID group column ending in 'O'. Everything set here stays a
        normal editable field afterward — this only seeds the starting
        values."""
        # -- MCQ count + ranges --
        self.mcq_count_spin.blockSignals(True)
        self.mcq_count_spin.setValue(self.SHAMEL_MCQ_COUNT)
        self.mcq_count_spin.blockSignals(False)
        self.range_builder.load_ranges(self.SHAMEL_MCQ_COUNT, self.SHAMEL_MCQ_RANGES)

        # -- Essays --
        self.essay_checkbox.blockSignals(True)
        self.essay_checkbox.setChecked(True)
        self.essay_checkbox.blockSignals(False)
        self.essay_count_lbl.setVisible(True)
        self.essay_count_spin.setVisible(True)
        self.essay_count_spin.setEnabled(True)
        self.essay_container.setVisible(True)

        self.essay_count_spin.blockSignals(True)
        self.essay_count_spin.setValue(self.SHAMEL_ESSAY_COUNT)
        self.essay_count_spin.blockSignals(False)
        self.generate_essay_inputs()
        for spin in self.essay_spinboxes:
            spin.setValue(self.SHAMEL_ESSAY_POINTS)

        # -- ID letters --
        self.id_letter_count_spin.blockSignals(True)
        self.id_letter_count_spin.setValue(self.SHAMEL_ID_LETTER_COUNT)
        self.id_letter_count_spin.blockSignals(False)
        self.id_letters_edit.setText(self.SHAMEL_ID_LETTERS)

        self._refresh_grand_total()

    def toggle_essay_inputs(self, checked):
        self.essay_count_lbl.setVisible(checked)
        self.essay_count_spin.setVisible(checked)
        self.essay_count_spin.setEnabled(checked)
        self.essay_container.setVisible(checked)
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
            show_warning(
                self, self.fonts.orbitron, self.fonts.mono, "Incomplete Mark Ranges",
                "Every MCQ question must be covered by a mark range before saving."
            )
            return

        id_letters = self.id_letters_edit.text().strip().upper()
        id_letter_count = self.id_letter_count_spin.value()
        if len(id_letters) != id_letter_count:
            show_warning(
                self, self.fonts.orbitron, self.fonts.mono, "ID Letters Mismatch",
                f"ID Letter Count is set to {id_letter_count}, but the ID Letters "
                f"field has {len(id_letters)} character(s). Make them match before saving."
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
            choices_per_question=self.choices_per_question_spin.value(),
            id_letters=id_letters,
        )

        show_info(
            self, self.fonts.orbitron, self.fonts.mono, "Success",
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
        # Blocked: setting this programmatically must NOT trigger
        # _on_exam_mode_changed, or reopening a saved Shamel-mode project
        # would immediately overwrite its real saved ranges/essays/ID
        # letters with the fresh-pick defaults below.
        self.exam_mode_combo.blockSignals(True)
        self.exam_mode_combo.setCurrentIndex(idx)
        self.exam_mode_combo.blockSignals(False)

        mcq_count = config.get("mcq_count", 10)
        self.mcq_count_spin.blockSignals(True)
        self.mcq_count_spin.setValue(mcq_count)
        self.mcq_count_spin.blockSignals(False)

        saved_ranges = config.get("mcq_ranges", [])
        if saved_ranges:
            self.range_builder.load_ranges(mcq_count, saved_ranges)
        else:
            self.range_builder.set_total_questions(mcq_count)

        self.choices_per_question_spin.setValue(config.get("choices_per_question", 4))

        id_letters = config.get("id_letters", self.DEFAULT_LETTERS)
        self.id_letters_edit.setText(id_letters)
        self.id_letter_count_spin.blockSignals(True)
        self.id_letter_count_spin.setValue(len(id_letters) or 1)
        self.id_letter_count_spin.blockSignals(False)

        has_essays = config.get("has_essays", False)
        essay_map = config.get("essay_points_map", {})

        self.essay_checkbox.blockSignals(True)
        self.essay_checkbox.setChecked(has_essays)
        self.essay_checkbox.blockSignals(False)
        self.essay_count_lbl.setVisible(has_essays)
        self.essay_count_spin.setVisible(has_essays)
        self.essay_count_spin.setEnabled(has_essays)
        self.essay_container.setVisible(has_essays)

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