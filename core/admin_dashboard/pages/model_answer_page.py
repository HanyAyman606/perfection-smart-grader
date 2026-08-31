"""
pages/model_answer_page.py
-----------------------------
"Model Answer Key" page: one BubbleRow per MCQ question, count driven by
the already-saved Exam Blueprint (mcq_count) — this page never asks for
a question count itself, so the exam's shape is defined in exactly one
place (the Setup page).

Voided questions are stored separately from the answer map (a list of
question numbers) rather than as a magic answer value like None, so the
mobile/grading side can skip them outright without special-casing what
"no answer" means.
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QGridLayout, QScrollArea, QPushButton,
    QMessageBox, QLabel
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import CLOUDY_SKY, NEON_PINK, BG_PANEL, WARN_COLOR, TEXT_MUTED
from admin_dashboard.pages.base import build_page_shell
from admin_dashboard.widgets.bubble_row import BubbleRow
from admin_dashboard.widgets.styled import make_outline_button
from admin_dashboard.exam_modes import DEFAULT_MODE_ID, SINGLE_VERSION_KEY, get_mode_by_id
from admin_dashboard.screens.dialogs import show_warning, show_info
from admin_dashboard.screens.dialogs import show_warning, show_info, ask_yes_no

NEXT_VERSION_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class ModelAnswerPage(QWidget):
    """One page, two behaviors depending on the active exam mode's
    `has_answer_versions` flag:
      - Quiz mode: a single implicit version (SINGLE_VERSION_KEY), version
        tabs stay hidden — behaves exactly like before.
      - Shamel mode: the blueprint (mcq_count/ranges/essays) is shared, but
        each booklet version gets its own answer key. Switching tabs just
        swaps the values shown in the same BubbleRow widgets — the grid
        itself is only rebuilt when mcq_count changes, not on every tab
        click.
    On-disk shape is ALWAYS nested by version (see ProjectManager), so
    nothing here or downstream needs an if/else on mode id.
    """

    def __init__(self, fonts, project_manager):
        super().__init__()
        self.fonts = fonts
        self.project_manager = project_manager
        self.rows: list[BubbleRow] = []

        self.supports_versions = False
        self.versions: list[str] = [SINGLE_VERSION_KEY]
        self.active_version = SINGLE_VERSION_KEY
        # In-memory scratch space for versions not currently shown on screen.
        self.answers_by_version: dict[str, dict] = {}
        self.voided_by_version: dict[str, list] = {}
        self.version_buttons: dict[str, QPushButton] = {}

        content_layout = build_page_shell(self, "Model Answer Key", NEON_PINK, fonts.orbitron)
        self._build_ui(content_layout)

    def _build_ui(self, content_layout):
        orbitron = self.fonts.orbitron

        self.version_row = QHBoxLayout()
        self.version_row.setSpacing(8)
        self.version_tabs_container = QWidget()
        self.version_tabs_container.setLayout(self.version_row)
        self.version_tabs_container.setVisible(False)
        content_layout.addWidget(self.version_tabs_container)

        self.status_lbl = QLabel("")
        self.status_lbl.setFont(QFont(self.fonts.mono, 10))
        content_layout.addWidget(self.status_lbl)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("border: none; background: transparent;")
        self.rows_container = QWidget()
        self.rows_container.setStyleSheet("background: transparent;")
        self.rows_layout = QGridLayout(self.rows_container)
        self.rows_layout.setVerticalSpacing(10)
        self.rows_layout.setHorizontalSpacing(20)
        self.rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.rows_layout.setColumnStretch(0, 1)
        self.rows_layout.setColumnStretch(1, 1)
        self.rows_layout.setColumnStretch(2, 1)
        self.scroll.setWidget(self.rows_container)
        content_layout.addWidget(self.scroll)

        self.btn_save = make_outline_button(
            "💾 SAVE MODEL ANSWER KEY", orbitron, CLOUDY_SKY,
            font_size=12, padding="15px", extra_style="margin-top: 10px;",
        )
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self.save_model_answers)
        content_layout.addWidget(self.btn_save)

    # ------------------------------------------------------------------
    def build_rows(self):
        """Rebuilds the bubble sheet to match the current blueprint's MCQ
        count, and re-derives version support from the current exam mode.
        Called by the dashboard every time this page becomes active, so it
        stays in sync if the question count or mode changed since the last
        visit."""
        for row in self.rows:
            row.setParent(None)
        self.rows.clear()

        if not self.project_manager.is_active:
            self.status_lbl.setText("No active workspace.")
            self.version_tabs_container.setVisible(False)
            return

        config = self.project_manager.load_config()
        mcq_count = config.get("mcq_count", 0)
        num_choices = config.get("choices_per_question", 4)  # NEW
        mode = get_mode_by_id(config.get("mode", DEFAULT_MODE_ID))
        self.supports_versions = mode.has_answer_versions

        # Quiz mode: even split across columns (Bubble Sheet Studio's
        # col1 = ceil(n/3) design). Shamel mode: fixed-size columns
        # (10 questions per column, same as the sync packet sent to
        # mobile) — so the answer-key input grid here always mirrors the
        # printed sheet's actual column layout, in either mode.
        if self.supports_versions:
            layout_cols = self.project_manager.compute_mcq_column_layout_fixed(mcq_count, num_cols=6, col_size=10)
        else:
            layout_cols = self.project_manager.compute_mcq_column_layout(mcq_count)
        col_sizes = [layout_cols["columns"][str(c)] for c in range(1, layout_cols["num_cols"] + 1)]

        col_bounds = []
        start = 1
        for size in col_sizes:
            col_bounds.append((start, start + size - 1))
            start += size

        for i in range(1, mcq_count + 1):
            row = BubbleRow(i, self.fonts.orbitron, self.fonts.mono,
                            num_choices=num_choices, compact=True)
            row.changed.connect(self._refresh_status)
            self.rows.append(row)
            for grid_col, (col_start, col_end) in enumerate(col_bounds):
                if col_start <= i <= col_end:
                    grid_row = i - col_start
                    break
            self.rows_layout.addWidget(row, grid_row, grid_col)

        saved_answers = config.get("model_answers", {})
        saved_voided = config.get("voided_questions", {})
        # Back-compat: projects saved before answer-versioning was added
        # stored these flat ({"1": "A"} / [4, 7]) instead of nested by
        # version. Wrap them under SINGLE_VERSION_KEY so old projects keep
        # working instead of crashing on .get().
        if isinstance(saved_answers, list) or (saved_answers and not isinstance(next(iter(saved_answers.values()), {}), dict)):
            saved_answers = {SINGLE_VERSION_KEY: saved_answers}
        if isinstance(saved_voided, list):
            saved_voided = {SINGLE_VERSION_KEY: saved_voided}

        self.versions = config.get("answer_versions") or [SINGLE_VERSION_KEY]
        if not self.supports_versions:
            self.versions = [SINGLE_VERSION_KEY]
        self.answers_by_version = {v: dict(saved_answers.get(v, {})) for v in self.versions}
        self.voided_by_version = {v: list(saved_voided.get(v, [])) for v in self.versions}

        self._rebuild_version_tabs()
        self._switch_version(self.versions[0], save_previous=False)

    def _rebuild_version_tabs(self):
        while self.version_row.count():
            item = self.version_row.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
        self.version_buttons.clear()

        self.version_tabs_container.setVisible(self.supports_versions)
        if not self.supports_versions:
            return

        for version in self.versions:
            tab_container = QWidget()
            tab_layout = QHBoxLayout(tab_container)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.setSpacing(2)

            btn = QPushButton(f"Booklet {version}")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFont(QFont(self.fonts.orbitron, 10, QFont.Weight.Bold))
            btn.setStyleSheet(self._tab_style())
            btn.clicked.connect(lambda checked, v=version: self._switch_version(v))
            tab_layout.addWidget(btn)
            self.version_buttons[version] = btn

            if len(self.versions) > 1:
                btn_remove = QPushButton("✕")
                btn_remove.setFixedSize(22, 22)
                btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_remove.setFont(QFont(self.fonts.orbitron, 9, QFont.Weight.Bold))
                btn_remove.setStyleSheet(f"""
                    QPushButton {{ background-color: {BG_PANEL}; color: {TEXT_MUTED};
                    border: 2px solid {TEXT_MUTED}; border-radius: 6px; padding: 0px; }}
                    QPushButton:hover {{ background-color: {WARN_COLOR}; color: #ffffff; border-color: {WARN_COLOR}; }}
                """)
                btn_remove.clicked.connect(lambda checked, v=version: self._remove_version(v))
                tab_layout.addWidget(btn_remove)

            self.version_row.addWidget(tab_container)

        btn_add = QPushButton("＋ ADD VERSION")
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.setFont(QFont(self.fonts.orbitron, 10, QFont.Weight.Bold))
        btn_add.setStyleSheet(self._tab_style())
        btn_add.clicked.connect(self._add_version)
        self.version_row.addWidget(btn_add)
        self.version_row.addStretch()

    def _tab_style(self) -> str:
        return f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {TEXT_MUTED};
                border: 2px solid {TEXT_MUTED}; border-radius: 8px; padding: 8px 14px;
            }}
            QPushButton:checked {{ background-color: {NEON_PINK}; color: #ffffff; border-color: {NEON_PINK}; }}
            QPushButton:hover {{ border-color: {NEON_PINK}; }}
        """

    def _add_version(self):
        used = set(self.versions)
        next_label = next((c for c in NEXT_VERSION_LETTERS if c not in used), None)
        if next_label is None:
            show_warning(self, self.fonts.orbitron, self.fonts.mono, "Limit Reached",
                         "No more version letters available.")
            return

        self._capture_active_version()
        self.versions.append(next_label)
        self.answers_by_version[next_label] = {}
        self.voided_by_version[next_label] = []
        self._rebuild_version_tabs()
        self._switch_version(next_label, save_previous=False)

    def _remove_version(self, version: str):
        if len(self.versions) <= 1:
            return  # never leave a Shamel exam with zero booklets

        confirmed = ask_yes_no(
            self, self.fonts.orbitron, self.fonts.mono, "Remove Booklet",
            f"Remove Booklet {version}? Its saved answers will be lost."
        )
        if not confirmed:
            return

        self.versions.remove(version)
        self.answers_by_version.pop(version, None)
        self.voided_by_version.pop(version, None)

        self._rebuild_version_tabs()
        if self.active_version == version:
            self._switch_version(self.versions[0], save_previous=False)
        else:
            self._refresh_status()

    def _capture_active_version(self):
        """Reads whatever is currently on screen into the in-memory dicts
        for self.active_version, before the rows get repainted for a
        different version. Only rows with an actual selected answer get a
        dict entry — an untouched row must be genuinely absent from the
        dict, not present with a None value, or the completeness check
        (str(q) not in answers) would treat "key exists but unanswered"
        as answered."""
        self.answers_by_version[self.active_version] = {
            str(r.question_number): r.selected_answer()
            for r in self.rows if not r.is_voided() and r.selected_answer()
        }
        self.voided_by_version[self.active_version] = [r.question_number for r in self.rows if r.is_voided()]

    def _switch_version(self, version: str, save_previous: bool = True):
        if save_previous and self.active_version in self.answers_by_version:
            self._capture_active_version()

        self.active_version = version
        for v, btn in self.version_buttons.items():
            btn.setChecked(v == version)

        answers = self.answers_by_version.get(version, {})
        voided = set(self.voided_by_version.get(version, []))
        for row in self.rows:
            key = str(row.question_number)
            row.set_state(answers.get(key), row.question_number in voided)

        self._refresh_status()

    def _refresh_status(self):
        total = len(self.rows)
        answered = sum(1 for r in self.rows if r.selected_answer() or r.is_voided())
        voided_count = sum(1 for r in self.rows if r.is_voided())
        remaining = total - answered
        prefix = f"[Booklet {self.active_version}]  " if self.supports_versions else ""
        if remaining > 0:
            self.status_lbl.setStyleSheet(f"color: {WARN_COLOR};")
            self.status_lbl.setText(f"{prefix}⚠ {remaining} question(s) missing a model answer.  ·  {voided_count} voided")
        else:
            self.status_lbl.setStyleSheet(f"color: {CLOUDY_SKY};")
            self.status_lbl.setText(f"{prefix}✔ All {total} questions have a model answer.  ·  {voided_count} voided")

        self.btn_save.setEnabled(self._all_versions_complete())

    def _all_versions_complete(self) -> bool:
        """Mirrors save_model_answers()'s validation, but runs live on every
        change so the button itself reflects readiness instead of only
        failing after a click. Captures the currently-displayed version's
        live row state first — otherwise the active version's in-progress
        answers wouldn't be reflected until a tab switch commits them."""
        if not self.rows:
            return False

        self._capture_active_version()

        total = len(self.rows)
        for version in self.versions:
            answers = self.answers_by_version.get(version, {})
            voided = set(self.voided_by_version.get(version, []))
            missing = [q for q in range(1, total + 1) if q not in voided and str(q) not in answers]
            if missing:
                return False
        return True

    # ------------------------------------------------------------------
    def save_model_answers(self):
        if not self.project_manager.is_active:
            return

        self._capture_active_version()

        incomplete = {}
        for version in self.versions:
            answers = self.answers_by_version.get(version, {})
            voided = set(self.voided_by_version.get(version, []))
            missing = [q for q in range(1, len(self.rows) + 1) if q not in voided and str(q) not in answers]
            if missing:
                incomplete[version] = missing

        if incomplete:
            details = "\n".join(
                f"Booklet {v}: {qs}" if self.supports_versions else f"{qs}"
                for v, qs in incomplete.items()
            )
            show_warning(self, self.fonts.orbitron, self.fonts.mono, "Incomplete Answer Key",
        f"These questions have no model answer or void flag yet:\n{details}")
            return


        self.project_manager.save_model_answers(self.answers_by_version, self.voided_by_version)
        show_info(self, self.fonts.orbitron, self.fonts.mono, "Success", "Model answer key saved to project workspace.")