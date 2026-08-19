"""
screens/dialogs.py
--------------------
Themed replacements for QMessageBox.information/warning/critical/question
and QFileDialog — matches NewGroupDialog's visual language instead of
falling through to the OS-native dialog.
"""

import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QListView, QLineEdit, QAbstractItemView, QFileSystemModel, QComboBox
)
from PySide6.QtCore import Qt, QDir, QSize, QStorageInfo
from PySide6.QtGui import QFont

from admin_dashboard.theme import SKY_AQUA, TEXT_MUTED, NEON_PINK, BG_PANEL, BG_CARD, TRUE_AZURE, WARN_COLOR, CLOUDY_SKY,TEXT_FEED
from admin_dashboard.widgets.common import apply_card_shadow
from admin_dashboard.widgets.styled import make_outline_button, make_title_label


class ThemedFileBrowserDialog(QDialog):
    """Replaces Qt's own file/directory browser with one that actually
    matches the app's theme — a card grid instead of a native table view.
    One class, three modes, since the navigation/browsing logic (walk
    into folders, go up, style the grid) is identical across all three;
    only what "Choose" does at the end differs.
    """

    def __init__(self, parent, title, mode: str, name_filter="All Files (*)",
                 start_dir="", suggested_name=""):
        super().__init__(parent)
        self.mode = mode  # "open_file" | "save_file" | "select_directory"
        self._chosen_path = None
        self.current_dir = start_dir or QDir.homePath()

        self.setWindowTitle(title)
        self.setFixedSize(640, 520)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #f0f9ff;
                border-radius: 14px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 22, 24, 20)

        title_lbl = QLabel(title.upper())
        title_lbl.setStyleSheet(f"color: {SKY_AQUA}; font-weight: 900; letter-spacing: 1px; font-size: 14px;")
        layout.addWidget(title_lbl)

        nav_row = QHBoxLayout()
        btn_up = make_outline_button("⬆ UP", self.font().family(), TEXT_MUTED, padding="6px 12px", radius=6)
        btn_up.clicked.connect(self._go_up)
        nav_row.addWidget(btn_up)

        # Drive switcher — cdUp() alone can never leave a drive root (e.g.
        # "C:/"), so without this there's no way to reach D:, E:, a USB
        # stick, etc. QStorageInfo.mountedVolumes() is used instead of
        # QDir.drives(), which is unreliable and can under-report mounted
        # drives on some systems. isValid()/isReady() filters out drives
        # that aren't actually accessible (e.g. empty optical drives,
        # disconnected network shares). It returns just the one root on
        # Linux/Mac, so the combo degrades harmlessly there.
        volumes = [v for v in QStorageInfo.mountedVolumes() if v.isValid() and v.isReady()]
        if len(volumes) > 1:
            self.drive_combo = QComboBox()
            self.drive_combo.setStyleSheet(f"""
                QComboBox {{ background-color: {BG_PANEL}; color: {TEXT_MUTED};
                border: 2px solid {TEXT_MUTED}; border-radius: 6px; padding: 6px 10px; }}
            """)
            current_drive_path = QDir(self.current_dir).rootPath()
            for vol in volumes:
                drive_path = vol.rootPath()
                self.drive_combo.addItem(drive_path, drive_path)
            idx = self.drive_combo.findData(current_drive_path)
            if idx >= 0:
                self.drive_combo.setCurrentIndex(idx)
            self.drive_combo.currentIndexChanged.connect(self._on_drive_changed)
            nav_row.addWidget(self.drive_combo)
        else:
            self.drive_combo = None

        self.path_label = QLineEdit(self.current_dir)
        self.path_label.setStyleSheet(f"""
            QLineEdit {{ background-color: transparent; color: {TEXT_MUTED};
            font-family: monospace; font-size: 11px; border: 1px solid transparent;
            border-radius: 4px; padding: 2px 4px; }}
            QLineEdit:focus {{ background-color: {BG_CARD}; border: 1px solid {TRUE_AZURE}; }}
        """)
        self.path_label.setToolTip("Type or paste a path and press Enter to navigate there directly — "
                                    "useful for drives that don't show up above (network shares, phones, etc.)")
        self.path_label.returnPressed.connect(self._on_path_entered)
        nav_row.addWidget(self.path_label, stretch=1)
        layout.addLayout(nav_row)

        self.model = QFileSystemModel()
        # Empty root path = whole filesystem, not just the C:/ drive.
        # Pinning this to QDir.rootPath() (which is "C:/" on Windows)
        # kept indexes for other drives (D:/, E:/, etc.) from resolving
        # correctly in some Qt versions.
        self.model.setRootPath("")
        if mode == "select_directory":
            self.model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)
        else:
            filters = self._parse_filter(name_filter)
            if filters:
                self.model.setNameFilters(filters)
                self.model.setNameFilterDisables(False)  # hide non-matching instead of graying out

        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setRootIndex(self.model.index(self.current_dir))
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setGridSize(QSize(112, 92))
        self.view.setSpacing(14)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setStyleSheet(f"""
            QListView {{
                background-color: transparent; color: {TEXT_MUTED};
                border: 2px solid {TRUE_AZURE}; border-radius: 10px; padding: 8px;
            }}
            QListView::item {{
                border: 2px solid {TRUE_AZURE}; border-radius: 10px; padding: 4px;
                color: {TEXT_FEED}; background-color: rgba(59,130,246,0.06);
            }}
            QListView::item:hover {{
                border: 2px solid {CLOUDY_SKY}; background-color: rgba(56,189,248,0.15);
            }}
            QListView::item:selected {{
                border: 2px solid {NEON_PINK}; background-color: rgba(236,72,153,0.15); color: {NEON_PINK};
            }}
        """)
        self.view.doubleClicked.connect(self._on_double_click)
        self.view.clicked.connect(self._on_click)
        layout.addWidget(self.view, stretch=1)

        self.filename_input = None
        if mode == "save_file":
            self.filename_input = QLineEdit(suggested_name)
            self.filename_input.setStyleSheet(f"""
                QLineEdit {{ background-color: {BG_CARD}; color: {TEXT_MUTED};
                border: 2px solid {TRUE_AZURE}; border-radius: 8px; padding: 10px; }}
                QLineEdit:focus {{ border: 2px solid {NEON_PINK}; }}
            """)
            self.filename_input.textChanged.connect(self._update_confirm_enabled)
            layout.addWidget(self.filename_input)

        btn_row = QHBoxLayout()
        btn_cancel = make_outline_button("✕ CANCEL", self.font().family(), TEXT_MUTED)
        btn_cancel.clicked.connect(self.reject)

        confirm_label = {"open_file": "✔ OPEN", "save_file": "💾 SAVE", "select_directory": "✔ CHOOSE THIS FOLDER"}[mode]
        self.btn_confirm = make_outline_button(confirm_label, self.font().family(), SKY_AQUA)
        self.btn_confirm.clicked.connect(self._confirm)
        self._update_confirm_enabled()

        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self.btn_confirm)
        layout.addLayout(btn_row)

    @staticmethod
    def _parse_filter(qt_filter_string: str) -> list[str]:
        """'Images (*.png *.jpg *.jpeg)' -> ['*.png', '*.jpg', '*.jpeg']"""
        if "(" not in qt_filter_string:
            return []
        inside = qt_filter_string.split("(", 1)[1].rstrip(")")
        return inside.split()

    def _navigate_to(self, path: str):
        self.current_dir = path
        self.view.setRootIndex(self.model.index(path))
        self.path_label.setText(path)
        self._sync_drive_combo(path)
        self._update_confirm_enabled()

    def _on_path_entered(self):
        """Manual fallback — lets the user reach any path the drive combo
        and folder grid can't, e.g. a network share, a phone's MTP path,
        or any drive that QStorageInfo didn't detect."""
        path = self.path_label.text().strip()
        if not path:
            return

        # Absolute paths (C:\..., \\server\share, /home/...) resolve as-is.
        # Relative paths (e.g. "Exams\Quiz1") must resolve against the
        # folder currently being browsed, not the process's working
        # directory — QDir(path).exists() alone checks the latter, which
        # in a frozen/onefile build is rarely what the user is looking at,
        # so a relative path would silently fail to navigate.
        candidate = QDir(path)
        if not candidate.isAbsolute():
            candidate = QDir(self.current_dir)
            if not candidate.cd(path):
                candidate = QDir(path)  # fall back to CWD-relative as a last resort

        if candidate.exists():
            self._navigate_to(candidate.absolutePath())
        else:
            # Invalid path — revert the text so it doesn't look like the
            # navigation silently succeeded.
            self.path_label.setText(self.current_dir)

    def _sync_drive_combo(self, path: str):
        if not self.drive_combo:
            return
        root = QDir(path).rootPath()
        idx = self.drive_combo.findData(root)
        if idx >= 0 and idx != self.drive_combo.currentIndex():
            self.drive_combo.blockSignals(True)
            self.drive_combo.setCurrentIndex(idx)
            self.drive_combo.blockSignals(False)

    def _on_drive_changed(self, index):
        drive_path = self.drive_combo.itemData(index)
        if drive_path:
            self._navigate_to(drive_path)

    def _go_up(self):
        d = QDir(self.current_dir)
        if d.cdUp():
            self._navigate_to(d.absolutePath())

    def _on_double_click(self, index):
        path = self.model.filePath(index)
        if self.model.isDir(index):
            self._navigate_to(path)
        elif self.mode == "open_file":
            self._chosen_path = path
            self.accept()

    def _on_click(self, index):
        if self.model.isDir(index):
            return
        if self.mode == "open_file":
            self._chosen_path = self.model.filePath(index)
            self._update_confirm_enabled()
        elif self.mode == "save_file":
            self.filename_input.setText(self.model.fileName(index))

    def _update_confirm_enabled(self):
        if self.mode == "open_file":
            self.btn_confirm.setEnabled(self._chosen_path is not None)
        elif self.mode == "save_file":
            self.btn_confirm.setEnabled(bool(self.filename_input.text().strip()))
        else:  # select_directory — the current folder itself is always a valid choice
            self.btn_confirm.setEnabled(True)

    def _confirm(self):
        if self.mode == "select_directory":
            self._chosen_path = self.current_dir
        elif self.mode == "save_file":
            name = self.filename_input.text().strip()
            if not name:
                return
            self._chosen_path = os.path.join(self.current_dir, name)
        self.accept()

    def result_path(self) -> str:
        return self._chosen_path or ""


class _ThemedMessageDialog(QDialog):
    def __init__(self, orbitron, mono, title, message, accent, parent=None, confirm_mode=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedWidth(420)
        self.setStyleSheet(f"QDialog {{ background-color: {BG_PANEL}; border-radius: 14px; }}")
        apply_card_shadow(self)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(30, 26, 30, 26)

        title_lbl = make_title_label(title.upper(), orbitron, accent, font_size=14)
        layout.addWidget(title_lbl)

        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setFont(QFont(mono, 10))
        msg_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        msg_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(msg_lbl)

        btn_row = QHBoxLayout()
        if confirm_mode:
            btn_no = make_outline_button("✕ CANCEL", orbitron, TEXT_MUTED)
            btn_no.clicked.connect(self.reject)
            btn_row.addWidget(btn_no)

        btn_yes = make_outline_button("✔ CONFIRM" if confirm_mode else "✔ OK", orbitron, accent)
        btn_yes.clicked.connect(self.accept)
        btn_row.addWidget(btn_yes)

        layout.addLayout(btn_row)
        btn_yes.setFocus()


def show_info(parent, orbitron, mono, title, message):
    _ThemedMessageDialog(orbitron, mono, title, message, SKY_AQUA, parent).exec()


def show_warning(parent, orbitron, mono, title, message):
    _ThemedMessageDialog(orbitron, mono, title, message, NEON_PINK, parent).exec()


def show_error(parent, orbitron, mono, title, message):
    _ThemedMessageDialog(orbitron, mono, title, message, WARN_COLOR, parent).exec()


def ask_yes_no(parent, orbitron, mono, title, message) -> bool:
    dialog = _ThemedMessageDialog(orbitron, mono, title, message, TRUE_AZURE, parent, confirm_mode=True)
    return dialog.exec() == QDialog.DialogCode.Accepted


def open_file_dialog(parent, title, file_filter, start_dir=""):
    dialog = ThemedFileBrowserDialog(parent, title, mode="open_file", name_filter=file_filter, start_dir=start_dir)
    return dialog.result_path() if dialog.exec() == QDialog.DialogCode.Accepted else ""


def save_file_dialog(parent, title, file_filter, default_name=""):
    dialog = ThemedFileBrowserDialog(parent, title, mode="save_file", name_filter=file_filter, suggested_name=default_name)
    return dialog.result_path() if dialog.exec() == QDialog.DialogCode.Accepted else ""


def open_directory_dialog(parent, title):
    dialog = ThemedFileBrowserDialog(parent, title, mode="select_directory")
    return dialog.result_path() if dialog.exec() == QDialog.DialogCode.Accepted else ""