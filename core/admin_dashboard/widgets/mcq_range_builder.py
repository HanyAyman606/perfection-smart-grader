"""
widgets/mcq_range_builder.py
------------------------------
Lets the admin split "question 1..total_mcq" into contiguous mark-value
ranges (e.g. Q1-10 = 1 mark, Q11-15 = 2 marks, Q16-20 = 3 marks) instead
of one flat points-per-question value.

Design:
- MCQRangeRow: one row's UI only (start label, end spinbox, points
  spinbox, remove button). No validation logic — just emits `changed`.
- MCQRangeBuilder: owns the list of rows and ALL validation/derivation
  logic (auto-computing each row's start from the previous row's end,
  checking the ranges fully and exactly cover 1..total with no gaps or
  overlaps). Kept separate from SetupPage so this behavior is reusable
  and testable independent of the page it happens to live on.
"""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QDoubleSpinBox, QPushButton
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import INPUT_STYLE, TEXT_MUTED, WARN_COLOR, SKY_AQUA, BG_PANEL


class MCQRangeRow(QWidget):
    changed = Signal()
    remove_requested = Signal(object)  # emits self

    def __init__(self, start: int, max_end: int, orbitron, mono, default_end: int = None, parent=None):
        super().__init__(parent)
        default_end = max_end if default_end is None else default_end

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.start_lbl = QLabel(f"Q{start}")
        self.start_lbl.setFont(QFont(mono, 11, QFont.Weight.Bold))
        self.start_lbl.setStyleSheet(f"color: {TEXT_MUTED}; background: transparent; border: none;")
        self.start_lbl.setFixedWidth(50)

        to_lbl = QLabel("to")
        to_lbl.setStyleSheet(f"color: {TEXT_MUTED}; background: transparent; border: none;")
        to_lbl.setFixedWidth(24)
        to_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.end_spin = QSpinBox()
        self.end_spin.setRange(start, max_end)
        self.end_spin.setValue(default_end)
        self.end_spin.setStyleSheet(INPUT_STYLE)
        self.end_spin.valueChanged.connect(lambda _: self.changed.emit())

        eq_lbl = QLabel("=")
        eq_lbl.setStyleSheet(f"color: {TEXT_MUTED}; background: transparent; border: none;")
        eq_lbl.setFixedWidth(16)
        eq_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.points_spin = QDoubleSpinBox()
        self.points_spin.setRange(0.25, 50.0)
        self.points_spin.setSingleStep(0.25)
        self.points_spin.setValue(1.0)
        self.points_spin.setSuffix(" pts")
        self.points_spin.setStyleSheet(INPUT_STYLE)
        self.points_spin.valueChanged.connect(lambda _: self.changed.emit())

        self.btn_remove = QPushButton("✕")
        self.btn_remove.setFixedWidth(34)
        self.btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_remove.setStyleSheet(f"""
            QPushButton {{ background-color: {BG_PANEL}; color: {WARN_COLOR};
                border: 2px solid {WARN_COLOR}; border-radius: 8px; }}
            QPushButton:hover {{ background-color: {WARN_COLOR}; color: #ffffff; }}
        """)
        self.btn_remove.clicked.connect(lambda: self.remove_requested.emit(self))

        layout.addWidget(self.start_lbl)
        layout.addWidget(to_lbl)
        layout.addWidget(self.end_spin)
        layout.addWidget(eq_lbl)
        layout.addWidget(self.points_spin)
        layout.addWidget(self.btn_remove)

    def set_start(self, start: int):
        self.start_lbl.setText(f"Q{start}")
        self.end_spin.setMinimum(start)
        if self.end_spin.value() < start:
            self.end_spin.setValue(start)

    def set_max_end(self, max_end: int):
        self.end_spin.setMaximum(max_end)

    @property
    def start(self) -> int:
        return int(self.start_lbl.text().lstrip("Q"))

    @property
    def end(self) -> int:
        return self.end_spin.value()

    @property
    def points(self) -> float:
        return self.points_spin.value()


class MCQRangeBuilder(QWidget):
    """Owns N MCQRangeRow widgets and keeps them a valid, gapless partition
    of 1..total_questions. Call set_total_questions() whenever the page's
    MCQ-count spinbox changes."""

    changed = Signal()

    def __init__(self, orbitron, mono, parent=None):
        super().__init__(parent)
        self.orbitron = orbitron
        self.mono = mono
        self.total_questions = 0
        self.rows: list[MCQRangeRow] = []

        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(8)

        self.status_lbl = QLabel("")
        self.status_lbl.setFont(QFont(self.mono, 10))
        self.layout_.addWidget(self.status_lbl)

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(8)
        self.layout_.addLayout(self.rows_layout)

        self.btn_add_range = QPushButton("+ ADD MARK RANGE")
        self.btn_add_range.setFont(QFont(orbitron, 10, QFont.Weight.Bold))
        self.btn_add_range.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add_range.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: {SKY_AQUA};
                border: 2px dashed {SKY_AQUA}; border-radius: 8px; padding: 10px; }}
            QPushButton:hover {{ color: #ffffff; background-color: {SKY_AQUA}; border-style: solid; }}
        """)
        self.btn_add_range.clicked.connect(self.add_range)
        self.layout_.addWidget(self.btn_add_range)

    # ------------------------------------------------------------------
    def set_total_questions(self, total: int):
        """Called whenever the MCQ-count spinbox changes. Trims/drops only
        the ranges that no longer fit instead of resetting everything."""
        self.total_questions = total

        if not self.rows:
            if total > 0:
                self._add_row(start=1, max_end=total)
            self._refresh_status()
            return

        # Drop rows that start beyond the new total entirely.
        for row in [r for r in self.rows if r.start > total]:
            self.rows.remove(row)
            row.setParent(None)

        # Clamp the last remaining row's end to the new total, if needed.
        if self.rows and self.rows[-1].end > total:
            self.rows[-1].set_max_end(total)
            self.rows[-1].end_spin.setValue(total)

        if not self.rows and total > 0:
            self._add_row(start=1, max_end=total)

        self._recompute_starts()
        self._refresh_status()

    def load_ranges(self, total: int, ranges: list[dict]):
        """Rebuilds rows from previously-saved range data (reopening an
        existing workspace) instead of collapsing to one default range."""
        self.total_questions = total
        for row in self.rows:
            row.setParent(None)
        self.rows.clear()

        for r in ranges:
            self._add_row(start=r["start"], max_end=total)
            self.rows[-1].end_spin.setValue(r["end"])
            self.rows[-1].points_spin.setValue(r["points"])

        self._recompute_starts()
        self._refresh_status()


    def _next_start(self) -> int:
        return (self.rows[-1].end + 1) if self.rows else 1

    def add_range(self):
        start = self._next_start()
        if start > self.total_questions:
            return
        self._add_row(start=start, max_end=self.total_questions, default_end=start)
        self._refresh_status()

    def _add_row(self, start: int, max_end: int, default_end: int = None):
        row = MCQRangeRow(start, max_end, self.orbitron, self.mono, default_end=default_end)
        row.changed.connect(self._on_row_changed)
        row.remove_requested.connect(self._remove_row)
        self.rows.append(row)
        self.rows_layout.addWidget(row)

    def _remove_row(self, row: MCQRangeRow):
        if len(self.rows) <= 1:
            return  # always keep at least one range
        self.rows.remove(row)
        row.setParent(None)
        self._recompute_starts()
        self._refresh_status()

    def _on_row_changed(self):
        self._recompute_starts()
        self._refresh_status()

    def _recompute_starts(self):
        expected_start = 1
        for row in self.rows:
            row.set_start(expected_start)
            expected_start = row.end + 1
        if self.rows:
            self.rows[-1].set_max_end(self.total_questions)

    def _refresh_status(self):
        covered = self.rows[-1].end if self.rows else 0
        remaining = self.total_questions - covered
        total_pts = self.total_points()
        if remaining > 0:
            self.status_lbl.setStyleSheet(f"color: {WARN_COLOR};")
            self.status_lbl.setText(
                f"⚠ {remaining} question(s) not yet assigned a mark range.  ·  MCQ total so far: {total_pts:g} pts"
            )
            self.btn_add_range.setEnabled(True)
        else:
            self.status_lbl.setStyleSheet(f"color: {SKY_AQUA};")
            self.status_lbl.setText(f"✔ All {self.total_questions} questions assigned  ·  MCQ total: {total_pts:g} pts")
            self.btn_add_range.setEnabled(False)
        self.changed.emit()

    # ------------------------------------------------------------------
    def is_valid(self) -> bool:
        return bool(self.rows) and self.total_questions > 0 and self.rows[-1].end == self.total_questions

    def get_ranges(self) -> list[dict]:
        """[{'start': 1, 'end': 10, 'points': 1.0}, ...] — saved into
        nexus_project.json and forwarded to mobiles in the sync packet."""
        return [{"start": r.start, "end": r.end, "points": r.points} for r in self.rows]

    def total_points(self) -> float:
        """Sum of (question count in range × points) across all rows —
        used by SetupPage for the live 'Total Exam Score' summary."""
        return sum((r.end - r.start + 1) * r.points for r in self.rows)