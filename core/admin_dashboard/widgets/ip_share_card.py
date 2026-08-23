"""
widgets/ip_share_card.py
--------------------------
The sidebar's "MOBILE CONNECT IP" card: shows this machine's LAN IP plus a
QR code phones can scan to connect, with copy/save actions and a refresh
button. Previously this was ~90 lines of construction plus five handler
methods (_refresh_ip_display, _copy_ip_to_clipboard, _copy_qr_to_clipboard,
_save_qr_image, _flash_ip_feedback) and four instance attributes living
directly on CyberpunkDashboard — a fully self-contained feature that had
no reason to be part of the main window's class body. It's a widget like
any other in widgets/; the dashboard just places it in the sidebar and
doesn't need to know how it works internally.
"""

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QApplication,
    QFileDialog, QGraphicsOpacityEffect,
)
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation
from PySide6.QtGui import QFont

from admin_dashboard.theme import SKY_AQUA, TEXT_MUTED, BG_PANEL
from admin_dashboard.widgets.qr_code import generate_qr_pixmap
from admin_dashboard.network_utils import get_local_ip


class NetworkChangeFlash(QLabel):
    """Small pulsing flash shown briefly above the IP card the moment the
    LAN IP address changes underneath it (Wi-Fi switch, VPN toggle,
    docking/undocking, etc.) — a lightweight nudge that the QR/IP below
    just auto-updated, so the admin doesn't have to spot the value change
    on their own.

    Runs a FINITE opacity animation (3 pulses over ~1.5s) rather than an
    always-on loop. PulsingDot in widgets/common.py was deliberately
    de-animated for exactly this cost reason (an infinite repaint loop
    per instance, "performance over decoration on slower machines") — this
    widget honors that same constraint by only ever animating for a
    moment, on a real change, then going fully idle (hidden, no timer,
    no repaints) until the next one."""

    def __init__(self, color_hex, mono_family, parent=None):
        super().__init__(parent)
        self.setText("⚡ NETWORK CHANGED — RESYNCED")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFont(QFont(mono_family, 8, QFont.Weight.Bold))
        self.setStyleSheet(
            f"color: {color_hex}; letter-spacing: 1px; background: transparent; border: none;"
        )

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)
        self.setVisible(False)

        self._animation = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._animation.setDuration(1000)
        self._animation.setKeyValueAt(0.0, 0.15)
        self._animation.setKeyValueAt(0.5, 1.0)
        self._animation.setKeyValueAt(1.0, 0.15)
        self._animation.setLoopCount(5)  # ~1.5s total, then stops — never infinite
        self._animation.finished.connect(lambda: self.setVisible(False))

    def pulse(self):
        """Triggers the flash. Safe to call again mid-pulse (e.g. the
        network flaps twice in quick succession) — restarts cleanly."""
        self.setVisible(True)
        self._animation.stop()
        self._opacity_effect.setOpacity(0.15)
        self._animation.start()


class IpShareCard(QFrame):
    def __init__(self, mono_family, orbitron_family, parent=None, *, poll_interval_ms=2000):
        super().__init__(parent)
        self.mono_family = mono_family
        self.orbitron_family = orbitron_family

        self.setStyleSheet(
            f"QFrame {{ background-color: {BG_PANEL}; border: 2px solid {SKY_AQUA}; border-radius: 10px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)

        self.network_flash = NetworkChangeFlash(SKY_AQUA, mono_family)
        layout.addWidget(self.network_flash)

        title_row = QHBoxLayout()
        title = QLabel("MOBILE CONNECT IP")
        title.setFont(QFont(mono_family, 8, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {TEXT_MUTED}; letter-spacing: 1px; background: transparent; border: none;")

        btn_refresh = QPushButton("⟳")
        btn_refresh.setFixedSize(24, 24)
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; border: none; font-weight: bold; }}
            QPushButton:hover {{ color: {SKY_AQUA}; }}
        """)
        btn_refresh.clicked.connect(self._refresh_ip_display)

        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(btn_refresh)

        self.ip_value_lbl = QLabel(get_local_ip())
        self.ip_value_lbl.setFont(QFont(orbitron_family, 13, QFont.Weight.Black))
        self.ip_value_lbl.setStyleSheet(f"color: {SKY_AQUA}; background: transparent; border: none;")
        self.ip_value_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.ip_qr_lbl = QLabel()
        self.ip_qr_lbl.setFixedSize(140, 140)
        self.ip_qr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ip_qr_lbl.setStyleSheet("background: #ffffff; border-radius: 6px;")
        self.ip_qr_lbl.setPixmap(generate_qr_pixmap(self.ip_value_lbl.text(), box_size=5, fg=SKY_AQUA))

        share_row = QHBoxLayout()
        share_row.setSpacing(6)

        btn_copy_ip = QPushButton("📋")
        btn_copy_ip.setToolTip("Copy IP address")
        btn_qr_copy = QPushButton("🖼")
        btn_qr_copy.setToolTip("Copy QR code image")
        btn_qr_save = QPushButton("💾")
        btn_qr_save.setToolTip("Save QR code as PNG")

        for btn in (btn_copy_ip, btn_qr_copy, btn_qr_save):
            btn.setFixedSize(28, 28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent; color: {TEXT_MUTED};
                    border: 1px solid {TEXT_MUTED}; border-radius: 6px;
                }}
                QPushButton:hover {{ color: {SKY_AQUA}; border-color: {SKY_AQUA}; }}
            """)

        btn_copy_ip.clicked.connect(self._copy_ip_to_clipboard)
        btn_qr_copy.clicked.connect(self._copy_qr_to_clipboard)
        btn_qr_save.clicked.connect(self._save_qr_image)

        share_row.addStretch()
        share_row.addWidget(btn_copy_ip)
        share_row.addWidget(btn_qr_copy)
        share_row.addWidget(btn_qr_save)
        share_row.addStretch()

        self.share_hint_lbl = QLabel("TAP TO SHARE")
        self.share_hint_lbl.setFont(QFont(mono_family, 7, QFont.Weight.Bold))
        self.share_hint_lbl.setStyleSheet(
            f"color: {TEXT_MUTED}; letter-spacing: 1px; background: transparent; border: none;"
        )
        self.share_hint_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        layout.addLayout(title_row)
        layout.addWidget(self.ip_value_lbl, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.ip_qr_lbl, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(6)
        layout.addLayout(share_row)
        layout.addWidget(self.share_hint_lbl)

        # Auto-resync: get_local_ip() is a UDP "connect" trick (routing-
        # table lookup only, no packet ever sent — see network_utils.py),
        # so it's cheap enough to poll on a plain QTimer without a worker
        # thread. Every tick just compares against the last-known IP;
        # nothing happens (no repaint, no flash) unless it actually
        # changed, so an idle network costs one near-instant socket call
        # every poll_interval_ms and nothing else.
        self._last_known_ip = self.ip_value_lbl.text()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._check_for_network_change)
        self._poll_timer.start(poll_interval_ms)

    def _check_for_network_change(self):
        current_ip = get_local_ip()
        if current_ip != self._last_known_ip:
            self._last_known_ip = current_ip
            self._refresh_ip_display()
            self.network_flash.pulse()

    def _refresh_ip_display(self):
        ip = get_local_ip()
        self.ip_value_lbl.setText(ip)
        self.ip_qr_lbl.setPixmap(generate_qr_pixmap(ip, box_size=5, fg=SKY_AQUA))
        # Keep the poller's baseline in sync — otherwise a manual refresh
        # (button click) that happens to catch a new IP first would leave
        # _last_known_ip stale, and the next poll tick would think the
        # network "changed again" and pulse a flash for a change that
        # already happened and was already shown to the admin.
        self._last_known_ip = ip

    def _copy_ip_to_clipboard(self):
        QApplication.clipboard().setText(self.ip_value_lbl.text())
        self._flash_feedback("✔ IP COPIED")

    def _copy_qr_to_clipboard(self):
        pixmap = self.ip_qr_lbl.pixmap()
        if pixmap is not None:
            QApplication.clipboard().setPixmap(pixmap)
            self._flash_feedback("✔ QR COPIED")

    def _save_qr_image(self):
        pixmap = self.ip_qr_lbl.pixmap()
        if pixmap is None:
            return
        default_name = f"connect_qr_{self.ip_value_lbl.text().replace('.', '-')}.png"
        path, _ = QFileDialog.getSaveFileName(self, "Save QR Code", default_name, "PNG Image (*.png)")
        if path:
            pixmap.save(path, "PNG")
            self._flash_feedback("✔ QR SAVED")

    def _flash_feedback(self, message: str):
        """Briefly swaps the share-row hint label to a confirmation, then
        reverts — cheap way to tell the admin the copy/save worked without
        popping a modal dialog over a sidebar action."""
        self.share_hint_lbl.setText(message)
        QTimer.singleShot(1400, lambda: self.share_hint_lbl.setText("TAP TO SHARE"))