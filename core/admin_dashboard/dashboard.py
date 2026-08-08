"""
dashboard.py
------------
CyberpunkDashboard: the QMainWindow that owns the master Login -> Welcome
-> Dashboard stack, the topbar/sidebar chrome, and page switching. All the
actual page content (Setup, Templates, Session Manager) lives in pages/;
all file/DB logic lives in ProjectManager. This class is now just wiring.
"""

from datetime import datetime

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QStackedWidget, QStackedLayout, QMessageBox, QDialog, QPushButton,
    QApplication, QFileDialog
)
from admin_dashboard.screens.new_project_dialog import NewProjectDialog
from admin_dashboard.screens.session_bank_dialog import SessionBankDialog
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QRect, QTimer

from admin_dashboard.theme import (
    Fonts, build_global_stylesheet, SKY_AQUA, TEXT_MUTED, CLOUDY_SKY,
    RASPBERRY_PLUM, NEON_PINK, INDIGO_BLOOM, BG_PANEL,ELECTRIC_SAPPHIRE
)
from admin_dashboard.project_manager import ProjectManager, CONFIG_FILENAME
from admin_dashboard.widgets.common import PulsingDot, NavButton, GridBackground, ScanlineOverlay
from admin_dashboard.widgets.qr_code import generate_qr_pixmap
from admin_dashboard.screens.login_screen import LoginScreen
from admin_dashboard.screens.welcome_screen import WelcomeScreen
from admin_dashboard.pages.setup_page import SetupPage
from admin_dashboard.pages.session_pages import SessionManagerPage
from admin_dashboard.pages.model_answer_page import ModelAnswerPage
from admin_dashboard.screens.dialogs import show_error
from admin_dashboard.network_utils import get_local_ip

NAV_SETUP, NAV_ANSWER_KEY, NAV_SESSION = range(3)
class CyberpunkDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Nexus Edge Grading System")
        self.resize(1300, 860)

        self.project_manager = ProjectManager()
        self.fonts = Fonts()
        self._page_animations = []

        central = QWidget()
        self.setCentralWidget(central)
        master_layout = QVBoxLayout(central)
        master_layout.setContentsMargins(0, 0, 0, 0)
        master_layout.setSpacing(0)

        self.master_stack = QStackedWidget()
        master_layout.addWidget(self.master_stack)

        self.login_screen = LoginScreen(self.handle_login_success, self.fonts.orbitron, self.fonts.mono)
        self.welcome_screen = WelcomeScreen(
            self.create_new_project, self.open_existing_project, self.quick_open_project,
            self.fonts.orbitron, self.fonts.mono
        )
        self.main_dashboard_widget = self._build_main_dashboard()

        self.master_stack.addWidget(self.login_screen)          # Index 0
        self.master_stack.addWidget(self.welcome_screen)        # Index 1
        self.master_stack.addWidget(self.main_dashboard_widget)  # Index 2

        self.setStyleSheet(build_global_stylesheet())
        self.master_stack.setCurrentIndex(0)

    def handle_login_success(self):
        self.welcome_screen.refresh_last_session_card()
        self.master_stack.setCurrentIndex(1)

    def quick_open_project(self, path: str):
        if self.project_manager.open_project(path):
            self.activate_dashboard()
        else:
            show_error(self, self.fonts.orbitron, self.fonts.mono, "Error",
                       f"Invalid Workspace: {CONFIG_FILENAME} not found in this folder.")

    # ------------------------------------------------------------------
    # WORKSPACE MANAGER
    # ------------------------------------------------------------------
    def create_new_project(self):
        dialog = NewProjectDialog(self.fonts.orbitron, self.fonts.mono, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.project_manager.create_project(dialog.project_name, dialog.parent_dir)
        self.activate_dashboard()

    def open_existing_project(self):
        dialog = SessionBankDialog(self.fonts.orbitron, self.fonts.mono, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_path:
            return
        self.quick_open_project(dialog.selected_path)

    def activate_dashboard(self):
        self.master_stack.setCurrentIndex(2)
        self.page_setup.load_blueprint()
        self.set_active_page(NAV_SETUP)
        self.sub_brand.setText(f"// WORKSPACE: {self.project_manager.project_name.upper()}")

    def return_to_hub(self):
        """Leaves the active workspace and goes back to the Workspace Hub
        so the admin can pick or create a different project."""
        self.welcome_screen.refresh_last_session_card()
        self.master_stack.setCurrentIndex(1)

    def _refresh_ip_display(self):
        ip = get_local_ip()
        self.ip_value_lbl.setText(ip)
        self.ip_qr_lbl.setPixmap(generate_qr_pixmap(ip, box_size=5, fg=SKY_AQUA))

    def _copy_ip_to_clipboard(self):
        QApplication.clipboard().setText(self.ip_value_lbl.text())
        self._flash_ip_feedback("✔ IP COPIED")

    def _copy_qr_to_clipboard(self):
        pixmap = self.ip_qr_lbl.pixmap()
        if pixmap is not None:
            QApplication.clipboard().setPixmap(pixmap)
            self._flash_ip_feedback("✔ QR COPIED")

    def _save_qr_image(self):
        pixmap = self.ip_qr_lbl.pixmap()
        if pixmap is None:
            return
        default_name = f"connect_qr_{self.ip_value_lbl.text().replace('.', '-')}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save QR Code", default_name, "PNG Image (*.png)"
        )
        if path:
            pixmap.save(path, "PNG")
            self._flash_ip_feedback("✔ QR SAVED")

    def _flash_ip_feedback(self, message: str):
        """Briefly swaps the share-row hint label to a confirmation, then
        reverts — cheap way to tell the admin the copy/save worked without
        popping a modal dialog over a sidebar action."""
        self.ip_share_hint_lbl.setText(message)
        QTimer.singleShot(1400, lambda: self.ip_share_hint_lbl.setText("TAP TO SHARE"))


    # ------------------------------------------------------------------
    # DASHBOARD CHROME (topbar / sidebar / content)
    # ------------------------------------------------------------------
    def _build_main_dashboard(self):
        dashboard_container = QWidget()
        layout = QVBoxLayout(dashboard_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_topbar())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        body_layout.addWidget(self._build_sidebar())
        body_layout.addWidget(self._build_content_area())

        layout.addWidget(body)
        return dashboard_container

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def _build_topbar(self):
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(64)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(28, 0, 28, 0)
        layout.setSpacing(14)

        self.brand_label = QLabel("NEXUS EDGE")
        self.brand_label.setFont(_font(self.fonts.orbitron, 15, "Black"))
        self.brand_label.setStyleSheet(
            f"color: {SKY_AQUA}; letter-spacing: 3px; background: transparent; border: none;"
        )

        self.sub_brand = QLabel("// GRADING SYSTEM")
        self.sub_brand.setFont(_font(self.fonts.orbitron, 9, "DemiBold"))
        self.sub_brand.setStyleSheet(
            f"color: {TEXT_MUTED}; letter-spacing: 3px; background: transparent; border: none;"
        )

        layout.addWidget(self.brand_label)
        layout.addWidget(self.sub_brand)
        layout.addStretch()

        status_dot = PulsingDot(SKY_AQUA, 10)
        status_lbl = QLabel("SYSTEM ONLINE")
        status_lbl.setFont(_font(self.fonts.orbitron, 9, "Bold"))
        status_lbl.setStyleSheet(
            f"color: {SKY_AQUA}; letter-spacing: 1px; background: transparent; border: none;"
        )

        self.clock_lbl = QLabel()
        self.clock_lbl.setFont(_font(self.fonts.mono, 10))
        self.clock_lbl.setStyleSheet(
            f"color: {TEXT_MUTED}; background: transparent; border: none; margin-left: 18px;"
        )
        self._update_clock()
        timer = QTimer(self)
        timer.timeout.connect(self._update_clock)
        timer.start(1000)

        layout.addWidget(status_dot)
        layout.addWidget(status_lbl)
        layout.addWidget(self.clock_lbl)

        return bar

    def _update_clock(self):
        self.clock_lbl.setText(datetime.now().strftime("%H:%M:%S"))

    def _build_sidebar(self):
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(256)

        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(20, 32, 20, 22)
        sidebar_layout.setSpacing(12)

        title_label = QLabel("NEURAL DECK")
        title_label.setObjectName("AppTitle")
        title_label.setFont(_font(self.fonts.orbitron, 16, "Black"))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar_layout.addWidget(title_label)
        sidebar_layout.addSpacing(14)

        btn_setup = NavButton("⚙  Exam Blueprint", CLOUDY_SKY, self.fonts.orbitron)
        btn_answer_key = NavButton("◉  Model Answer Key", INDIGO_BLOOM, self.fonts.orbitron)
        btn_live = NavButton("●  Session Manager", NEON_PINK, self.fonts.orbitron)

        self.nav_buttons = [btn_setup, btn_answer_key, btn_live]

        for btn in self.nav_buttons:
            sidebar_layout.addWidget(btn)
        sidebar_layout.addStretch()

        btn_switch_workspace = QPushButton("⤺ SWITCH WORKSPACE")
        btn_switch_workspace.setFont(_font(self.fonts.orbitron, 10, "Bold"))
        btn_switch_workspace.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_switch_workspace.setStyleSheet(f"""
                    QPushButton {{
                        background-color: transparent; color: {TEXT_MUTED};
                        border: 2px solid {TEXT_MUTED}; border-radius: 10px; padding: 12px;
                    }}
                    QPushButton:hover {{ background-color: {TEXT_MUTED}; color: #ffffff; }}
                """)
        btn_switch_workspace.clicked.connect(self.return_to_hub)
        sidebar_layout.addWidget(btn_switch_workspace)

        self.ip_card = QFrame()
        self.ip_card.setStyleSheet(f"""
                    QFrame {{ background-color: {BG_PANEL if 'BG_PANEL' in dir() else 'transparent'}; border: 2px solid {SKY_AQUA}; border-radius: 10px; }}
                """)
        ip_layout = QVBoxLayout(self.ip_card)
        ip_layout.setContentsMargins(12, 10, 12, 10)
        ip_layout.setSpacing(2)

        title_row = QHBoxLayout()
        ip_title = QLabel("MOBILE CONNECT IP")
        ip_title.setFont(_font(self.fonts.mono, 8, "Bold"))
        ip_title.setStyleSheet(f"color: {TEXT_MUTED}; letter-spacing: 1px; background: transparent; border: none;")

        btn_refresh_ip = QPushButton("⟳")
        btn_refresh_ip.setFixedSize(24, 24)
        btn_refresh_ip.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh_ip.setStyleSheet(f"""
                    QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; border: none; font-weight: bold; }}
                    QPushButton:hover {{ color: {SKY_AQUA}; }}
                """)
        btn_refresh_ip.clicked.connect(self._refresh_ip_display)

        title_row.addWidget(ip_title)
        title_row.addStretch()
        title_row.addWidget(btn_refresh_ip)

        self.ip_value_lbl = QLabel(get_local_ip())
        self.ip_value_lbl.setFont(_font(self.fonts.orbitron, 13, "Black"))
        self.ip_value_lbl.setStyleSheet(f"color: {SKY_AQUA}; background: transparent; border: none;")
        self.ip_value_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.ip_qr_lbl = QLabel()
        self.ip_qr_lbl.setFixedSize(140, 140)
        self.ip_qr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ip_qr_lbl.setStyleSheet("background: #ffffff; border-radius: 6px;")
        self.ip_qr_lbl.setPixmap(
            generate_qr_pixmap(self.ip_value_lbl.text(), box_size=5, fg=SKY_AQUA)
        )

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

        self.ip_share_hint_lbl = QLabel("TAP TO SHARE")
        self.ip_share_hint_lbl.setFont(_font(self.fonts.mono, 7, "Bold"))
        self.ip_share_hint_lbl.setStyleSheet(f"color: {TEXT_MUTED}; letter-spacing: 1px; background: transparent; border: none;")
        self.ip_share_hint_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        ip_layout.addLayout(title_row)
        ip_layout.addWidget(self.ip_value_lbl, alignment=Qt.AlignmentFlag.AlignHCenter)
        ip_layout.addWidget(self.ip_qr_lbl, alignment=Qt.AlignmentFlag.AlignHCenter)
        ip_layout.addSpacing(6)
        ip_layout.addLayout(share_row)
        ip_layout.addWidget(self.ip_share_hint_lbl)
        sidebar_layout.addWidget(self.ip_card)


        footer = QLabel("v2.4.1 · SECURE LINK")
        footer.setFont(_font(self.fonts.mono, 9))
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet(
            f"color: {TEXT_MUTED}; letter-spacing: 1px; background: transparent; border: none;"
        )
        sidebar_layout.addWidget(footer)

        self.indicator = QFrame(self.sidebar)
        self.indicator.setFixedWidth(4)
        self.indicator.setStyleSheet(f"background-color: {CLOUDY_SKY}; border-radius: 2px;")

        btn_setup.clicked.connect(lambda: self.set_active_page(NAV_SETUP))
        btn_answer_key.clicked.connect(lambda: self.set_active_page(NAV_ANSWER_KEY))
        btn_live.clicked.connect(lambda: self.set_active_page(NAV_SESSION))

        return self.sidebar

    def _move_indicator(self, button: NavButton):
        target = QRect(0, button.y(), 4, button.height())
        anim = QPropertyAnimation(self.indicator, b"geometry", self)
        anim.setDuration(280)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(self.indicator.geometry())
        anim.setEndValue(target)
        anim.start()
        self.indicator.setStyleSheet(
            f"background-color: {button.glow_color.name()}; border-radius: 2px;"
        )
        self._page_animations.append(anim)

    def _build_content_area(self):
        container = QWidget()
        container.setObjectName("ContentContainer")
        stack_layout = QStackedLayout(container)
        stack_layout.setStackingMode(QStackedLayout.StackingMode.StackAll)

        self.grid_bg = GridBackground()
        stack_layout.addWidget(self.grid_bg)

        self.content_area = QStackedWidget()
        self.content_area.setObjectName("ContentArea")

        self.page_setup = SetupPage(self.fonts, self.project_manager)
        self.page_answer_key = ModelAnswerPage(self.fonts, self.project_manager)
        self.page_session = SessionManagerPage(self.fonts, self.project_manager)

        self.content_area.addWidget(self.page_setup)
        self.content_area.addWidget(self.page_answer_key)
        self.content_area.addWidget(self.page_session)

        stack_layout.addWidget(self.content_area)
        stack_layout.setCurrentWidget(self.content_area)

        return container

    # ------------------------------------------------------------------
    # PAGE SWITCHING
    # ------------------------------------------------------------------
    def set_active_page(self, index):
        self.content_area.setCurrentIndex(index)

        if index == NAV_ANSWER_KEY:
            self.page_answer_key.build_rows()

        if index == NAV_SESSION:
            self.page_session.reset_to_hub()

        for i, btn in enumerate(self.nav_buttons):
            btn.set_active(i == index)

        active_btn = self.nav_buttons[index]
        QTimer.singleShot(0, lambda: self._move_indicator(active_btn))

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self._move_indicator(self.nav_buttons[0]))


def _font(family, size, weight_name="Normal"):
    """Tiny helper so topbar/sidebar building above doesn't need to import
    QFont.Weight.* directly at every call site."""
    from PySide6.QtGui import QFont
    weight = getattr(QFont.Weight, weight_name)
    return QFont(family, size, weight)