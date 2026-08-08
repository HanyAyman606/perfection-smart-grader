"""
screens/payload_preview_dialog.py
------------------------------------
Read-only preview of the exact sync packet about to be broadcast to
mobile clients before "Start Live Grading" opens the socket. Cheaper to
catch a mistake here (wrong mode, missing answers) than after graders
have already started scanning.
"""

import json

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QWidget, QFrame, QTextEdit
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from admin_dashboard.theme import SKY_AQUA, TEXT_MUTED, NEON_PINK, BG_PANEL, BG_CARD, TRUE_AZURE, CLOUDY_SKY
from admin_dashboard.exam_modes import get_mode_by_id


class PayloadPreviewDialog(QDialog):
    """exec() returns QDialog.DialogCode.Accepted if the admin confirms."""

    def __init__(self, packet: dict, orbitron, mono, parent=None):
        super().__init__(parent)
        self.packet = packet
        self.orbitron = orbitron
        self.mono = mono

        self.setWindowTitle("Confirm Sync Payload")
        self.setFixedSize(520, 610)
        self.setStyleSheet(f"QDialog {{ background-color: {BG_PANEL}; }}")

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(26, 24, 26, 24)

        title = QLabel("📡 PAYLOAD ABOUT TO BE SENT")
        title.setFont(QFont(orbitron, 14, QFont.Weight.Black))
        title.setStyleSheet(f"color: {CLOUDY_SKY}; letter-spacing: 1px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(10)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        mode = get_mode_by_id(packet.get("exam_mode", ""))
        mcq_count = packet.get("mcq_count", 0)
        ranges = packet.get("mcq_ranges", [])
        versions = packet.get("answer_versions", ["A"])
        answers_by_version = packet.get("model_answers", {})
        voided_by_version = packet.get("voided_questions", {})
        has_essays = packet.get("has_essays", False)
        essay_map = packet.get("essay_points_map", {})
        mcq_columns = packet.get("mcq_columns", {})

        rows = [
            ("Exam", packet.get("exam_name", "—")),
            ("Group", packet.get("group_name", "—")),
            ("Mode", mode.label),
            ("MCQ Questions", str(mcq_count)),
            ("Mark Ranges", ", ".join(f"Q{r['start']}-{r['end']}={r['points']:g}pt" for r in ranges) or "None"),
        ]
        for version in versions:
            answers = answers_by_version.get(version, {})
            voided = voided_by_version.get(version, [])
            label = f"Model Answers ({version})" if len(versions) > 1 else "Model Answers"
            rows.append((label, f"{len(answers)} answered · {len(voided)} voided · "
                                 f"{mcq_count - len(answers) - len(voided)} missing"))
        cols_summary = ", ".join(
            f"Col{c}:{n}" for c, n in mcq_columns.get("columns", {}).items()
        ) or "—"
        rows += [
            ("Essay Questions", f"{len(essay_map)} question(s)" if has_essays else "Not included"),
            ("Layout", f"{packet.get('choices_per_question', 4)} choices/Q · "
                       f"{''.join(packet.get('id', {}).get('letters', []))} ID letters · "
                       f"{packet.get('id', {}).get('num_digits', '?')} ID digits"),
            ("MCQ Columns", f"{mcq_columns.get('num_cols', 3)} cols  ({cols_summary})"),
        ]
        for label, value in rows:
            content_layout.addWidget(self._build_row(label, value, mono, orbitron))

        btn_view_json = QPushButton("🔍 VIEW RAW JSON")
        btn_view_json.setFont(QFont(orbitron, 11, QFont.Weight.Bold))
        btn_view_json.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_view_json.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {SKY_AQUA};
                border: 2px solid {SKY_AQUA}; border-radius: 8px; padding: 10px;
            }}
            QPushButton:hover {{ background-color: {SKY_AQUA}; color: #ffffff; }}
        """)
        btn_view_json.clicked.connect(self._show_raw_json)
        layout.addWidget(btn_view_json)

        btn_confirm = QPushButton("🚀 CONFIRM & START SERVER")
        btn_confirm.setFont(QFont(orbitron, 12, QFont.Weight.Bold))
        btn_confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_confirm.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {NEON_PINK};
                border: 2px solid {NEON_PINK}; border-radius: 8px; padding: 14px;
            }}
            QPushButton:hover {{ background-color: {NEON_PINK}; color: #ffffff; }}
        """)
        btn_confirm.clicked.connect(self.accept)
        layout.addWidget(btn_confirm)

        btn_cancel = QPushButton("CANCEL")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; border: none; padding: 8px; }}
            QPushButton:hover {{ color: #000000; }}
        """)
        btn_cancel.clicked.connect(self.reject)
        layout.addWidget(btn_cancel)

    def _build_row(self, label, value, mono, orbitron) -> QFrame:
        row = QFrame()
        row.setStyleSheet(f"""
            QFrame {{ background-color: {BG_CARD}; border: 1px solid {TRUE_AZURE}; border-radius: 8px; }}
        """)
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(14, 8, 14, 8)
        row_layout.setSpacing(2)

        lbl = QLabel(label.upper())
        lbl.setFont(QFont(mono, 8, QFont.Weight.Bold))
        lbl.setStyleSheet(f"color: {TEXT_MUTED}; background: transparent; border: none;")

        val = QLabel(str(value))
        val.setFont(QFont(orbitron, 11, QFont.Weight.Bold))
        val.setStyleSheet(f"color: {SKY_AQUA}; background: transparent; border: none;")
        val.setWordWrap(True)

        row_layout.addWidget(lbl)
        row_layout.addWidget(val)
        return row

    # ------------------------------------------------------------------
    def _show_raw_json(self):
        """Read-only popup showing the exact JSON that build_sync_packet()
        will hand to json.dumps() and send over the socket — useful for
        debugging the mobile side against the real payload shape."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Raw Sync Packet (JSON)")
        dialog.resize(640, 640)
        dialog.setStyleSheet(f"QDialog {{ background-color: {BG_PANEL}; }}")

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("📄 EXACT PAYLOAD JSON")
        title.setFont(QFont(self.orbitron, 13, QFont.Weight.Black))
        title.setStyleSheet(f"color: {CLOUDY_SKY}; letter-spacing: 1px;")
        layout.addWidget(title)

        text_box = QTextEdit()
        text_box.setReadOnly(True)
        text_box.setFont(QFont("Consolas", 10))
        text_box.setStyleSheet(f"""
            QTextEdit {{
                background-color: #0d1117; color: {SKY_AQUA};
                border: 1px solid {TRUE_AZURE}; border-radius: 8px; padding: 10px;
            }}
        """)
        text_box.setPlainText(json.dumps(self.packet, indent=2, ensure_ascii=False))
        layout.addWidget(text_box)

        btn_row = QHBoxLayout()
        btn_copy = QPushButton("📋 COPY TO CLIPBOARD")
        btn_copy.setFont(QFont(self.orbitron, 10, QFont.Weight.Bold))
        btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_copy.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {SKY_AQUA};
                border: 2px solid {SKY_AQUA}; border-radius: 8px; padding: 10px;
            }}
            QPushButton:hover {{ background-color: {SKY_AQUA}; color: #ffffff; }}
        """)
        btn_copy.clicked.connect(lambda: self._copy_text(text_box))

        btn_close = QPushButton("CLOSE")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; border: none; padding: 10px; }}
            QPushButton:hover {{ color: #ffffff; }}
        """)
        btn_close.clicked.connect(dialog.accept)

        btn_row.addWidget(btn_copy)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        dialog.exec()

    def _copy_text(self, text_box: QTextEdit):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text_box.toPlainText())