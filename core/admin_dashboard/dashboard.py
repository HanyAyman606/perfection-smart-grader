"""
dashboard.py
------------
CyberpunkDashboard: the QMainWindow that owns the master Login -> Welcome
-> Dashboard stack, the topbar/sidebar chrome, and page switching. All the
actual page content (Setup, Templates, Session Manager) lives in pages/;
all file/DB logic lives in ProjectManager; opening/creating a workspace
lives in WorkspaceController; the sidebar's IP/QR sharing lives in
widgets/ip_share_card.py. This class is now just wiring.
"""

from datetime import datetime

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QStackedWidget, QStackedLayout, QPushButton
)
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QRect, QTimer

from admin_dashboard.theme import (
    Fonts, build_global_stylesheet, SKY_AQUA, TEXT_MUTED, CLOUDY_SKY,
    NEON_PINK, INDIGO_BLOOM,
)
from admin_dashboard.project_manager import ProjectManager
from admin_dashboard.workspace_controller import WorkspaceController
from admin_dashboard.widgets.common import PulsingDot, NavButton, GridBackground, ScanlineOverlay
from admin_dashboard.widgets.ip_share_card import IpShareCard
from admin_dashboard.screens.login_screen import LoginScreen
from admin_dashboard.screens.welcome_screen import WelcomeScreen
from admin_dashboard.pages.setup_page import SetupPage
from admin_dashboard.pages.session_pages import SessionManagerPage
from admin_dashboard.pages.model_answer_page import ModelAnswerPage

NAV_SETUP, NAV_ANSWER_KEY, NAV_SESSION = range(3)
class CyberpunkDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Nexus Edge Grading System")
        self.resize(1300, 860)

        self.project_manager = ProjectManager()
        self.fonts = Fonts()
        self._page_animations = []
        self.workspace = WorkspaceController(self.project_manager, self.fonts, on_activated=self.activate_dashboard)

        central = QWidget()
        self.setCentralWidget(central)
        master_layout = QVBoxLayout(central)
        master_layout.setContentsMargins(0, 0, 0, 0)
        master_layout.setSpacing(0)

        self.master_stack = QStackedWidget()
        master_layout.addWidget(self.master_stack)

        self.login_screen = LoginScreen(self.handle_login_success, self.fonts.orbitron, self.fonts.mono)
        self.welcome_screen = WelcomeScreen(
            lambda: self.workspace.create_new_project(self),
            lambda: self.workspace.open_existing_project(self),
            lambda path: self.workspace.quick_open_project(path, self),
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

    # ------------------------------------------------------------------
    # WORKSPACE ACTIVATION (reached via WorkspaceController.on_activated)
    # ------------------------------------------------------------------
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

        self.ip_card = IpShareCard(self.fonts.mono, self.fonts.orbitron)
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