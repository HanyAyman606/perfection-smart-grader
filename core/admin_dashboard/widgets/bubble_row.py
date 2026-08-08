"""
widgets/bubble_row.py
------------------------
One row of the mock bubble sheet: a question number, a row of exclusive
A-F choice bubbles (the model answer), and a VOID flag for questions
later discovered to be wrong mid-exam — excluded from grading without
deleting the row or renumbering every question after it.

CHOICE_LETTERS is the single source of truth for how many bubbles each
row has. Change it here once (drop to A-D, extend to A-H) and every row
picks it up automatically — nothing else in the app hardcodes the letter
set, so this is the one place a future exam format change happens.
"""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QButtonGroup, QSizePolicy
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import TEXT_MUTED, SKY_AQUA, WARN_COLOR, BG_PANEL, TRUE_AZURE

CHOICE_LETTERS_MAX = ["A", "B", "C", "D", "E", "F", "G", "H"]
BUBBLE_SIZE = 38
BUBBLE_SIZE_COMPACT = 32  # used by the Model Answer page's 3-column layout
                          # (matches Bubble Sheet Studio) — sized so all 3
                          # columns fit the page width with no horizontal
                          # scrolling, while staying close to full size


class BubbleRow(QWidget):
    """Emits `changed` whenever the selected answer or void state changes.

    `num_choices` picks how many lettered bubbles this row renders (A..whatever),
    driven by the Exam Blueprint's "Choices per Q" setting so the answer key
    always matches the bubble sheet the students actually fill in. Clamped to
    CHOICE_LETTERS_MAX so a bad config value can't blow up the UI.
    """
    changed = Signal()

    def __init__(self, question_number: int, orbitron, mono, num_choices: int = 4,
                 compact: bool = False, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.question_number = question_number
        self._voided = False
        self.bubble_size = BUBBLE_SIZE_COMPACT if compact else BUBBLE_SIZE

        num_choices = max(2, min(num_choices, len(CHOICE_LETTERS_MAX)))
        self.choice_letters = CHOICE_LETTERS_MAX[:num_choices]

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8 if compact else 10)

        self.q_lbl = QLabel(f"Q{question_number}")
        self.q_lbl.setFixedWidth(36 if compact else 50)
        self.q_lbl.setFont(QFont(mono, 10 if compact else 11, QFont.Weight.Bold))
        self.q_lbl.setStyleSheet(f"color: {TEXT_MUTED}; background: transparent; border: none;")
        layout.addWidget(self.q_lbl)

        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)
        self.choice_buttons = {}

        for letter in self.choice_letters:
            btn = QPushButton(letter)
            btn.setCheckable(True)
            btn.setFixedSize(self.bubble_size, self.bubble_size)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFont(QFont(orbitron, 9 if compact else 11, QFont.Weight.Bold))
            btn.setStyleSheet(self._bubble_style())
            btn.clicked.connect(self.changed.emit)
            self.button_group.addButton(btn)
            self.choice_buttons[letter] = btn
            layout.addWidget(btn)

        layout.addStretch()

        self.btn_void = QPushButton("🚩 VOID")
        self.btn_void.setCheckable(True)
        self.btn_void.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_void.setFont(QFont(orbitron, 8 if compact else 9, QFont.Weight.Bold))
        self.btn_void.setStyleSheet(self._void_style(compact))
        self.btn_void.toggled.connect(self._on_void_toggled)
        layout.addWidget(self.btn_void)

    # ------------------------------------------------------------------
    def _bubble_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {TRUE_AZURE};
                border: 2px solid {TRUE_AZURE}; border-radius: {self.bubble_size // 2}px;
            }}
            QPushButton:checked {{
                background-color: {SKY_AQUA}; color: #ffffff; border: 2px solid {SKY_AQUA};
            }}
            QPushButton:hover {{ border: 2px solid {SKY_AQUA}; }}
            QPushButton:disabled {{
                background-color: #e2e8f0; color: {TEXT_MUTED}; border: 2px solid #e2e8f0;
            }}
        """

    def _void_style(self, compact: bool = False) -> str:
        padding = "4px 8px" if compact else "8px 12px"
        return f"""
            QPushButton {{
                background-color: transparent; color: {WARN_COLOR};
                border: 2px solid {WARN_COLOR}; border-radius: 8px; padding: {padding};
            }}
            QPushButton:checked {{ background-color: {WARN_COLOR}; color: #ffffff; }}
            QPushButton:hover {{ background-color: {WARN_COLOR}; color: #ffffff; }}
        """

    def _on_void_toggled(self, checked: bool):
        self._voided = checked
        for btn in self.choice_buttons.values():
            btn.setEnabled(not checked)

        font = self.q_lbl.font()
        font.setStrikeOut(checked)
        self.q_lbl.setFont(font)
        self.q_lbl.setStyleSheet(
            f"color: {WARN_COLOR if checked else TEXT_MUTED}; background: transparent; border: none;"
        )
        self.changed.emit()

    # ------------------------------------------------------------------
    def is_voided(self) -> bool:
        return self._voided

    def selected_answer(self) -> str | None:
        checked = self.button_group.checkedButton()
        return checked.text() if checked else None

    def set_state(self, answer: str | None, voided: bool):
        """Used by ModelAnswerPage to restore a previously-saved row (or to
        blank it out when switching to a version with no saved answer yet).

        Note: an exclusive QButtonGroup enforces "exactly one checked" once
        any button has been checked — calling setChecked(False) on the
        currently-checked button is silently ignored. Dropping exclusivity
        for the moment is the only way to actually clear the selection."""
        self.button_group.setExclusive(False)
        currently_checked = self.button_group.checkedButton()
        if currently_checked:
            currently_checked.setChecked(False)
        self.button_group.setExclusive(True)

        if answer and answer in self.choice_buttons:
            self.choice_buttons[answer].setChecked(True)
        self.btn_void.setChecked(voided)  # fires _on_void_toggled via its own signal