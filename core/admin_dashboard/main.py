import sys
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QStackedWidget, QStackedLayout, QFrame, QFileDialog, QInputDialog,
    QSizePolicy, QGraphicsView, QGraphicsScene, QLineEdit, QMessageBox, QScrollArea,
    QComboBox, QCheckBox, QDoubleSpinBox, QSpinBox, QListWidget
)
from PySide6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QRect, QRectF, QPointF,
    QTimer, Property, QThread, Signal
)
from PySide6.QtGui import (
    QColor, QPainter, QFont, QFontDatabase, QPen, QBrush, QPixmap,
    QImageReader, QTransform
)
import os
import shutil
import json
import sqlite3

# ---------------------------------------------------------------------------
# PALETTE  (same variable names/values as the Figma Make App.tsx)
# ---------------------------------------------------------------------------
NEON_PINK = "#f72585"
RASPBERRY_PLUM = "#b5179e"
INDIGO_BLOOM = "#7209b7"
ULTRASONIC_BLUE = "#560bad"
TRUE_AZURE = "#480ca8"
VIVID_ROYAL = "#3a0ca3"
PERSIAN_BLUE = "#3f37c9"
ELECTRIC_SAPPHIRE = "#4361ee"
CLOUDY_SKY = "#4895ef"
SKY_AQUA = "#4cc9f0"

BG_DEEP = "#080510"
BG_PANEL = "#120a24"
BG_CARD = "#16092b"
TEXT_MUTED = "#8b85b0"
TEXT_FEED = "#c8c4e8"
WARN_COLOR = "#ff6b8a"

# ---------------------------------------------------------------------------
# FONTS
# Orbitron / Rajdhani / JetBrains Mono are loaded from Google Fonts in the
# React version via @import url(...). Qt has no equivalent auto-fetch, so we
# check whether they're installed on the system and gracefully fall back to
# a system sans/mono if not. For pixel-perfect parity, install these three
# font families locally (e.g. via the Google Fonts website) before running.
# ---------------------------------------------------------------------------
def resolve_font(preferred: str, fallback: str) -> str:
    available = QFontDatabase.families()
    return preferred if preferred in available else fallback


# ---------------------------------------------------------------------------
# STATUS DOT  (static — the animated ping/breathe version was replaced to
# cut two infinite-loop repaint animations per instance; performance over
# decoration on slower machines)
# ---------------------------------------------------------------------------
class PulsingDot(QWidget):
    def __init__(self, color=NEON_PINK, size=10, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.base_size = size
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.color)
        painter.drawEllipse(0, 0, self.width(), self.height())


# ---------------------------------------------------------------------------
# NAV BUTTON
# ---------------------------------------------------------------------------
class NavButton(QPushButton):
    def __init__(self, text, glow_hex, orbitron_family, parent=None):
        super().__init__(text, parent)
        self.glow_color = QColor(glow_hex)
        self.is_active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(48)
        self.setFont(QFont(orbitron_family, 10, QFont.Weight.Bold))

        self._base_style = f"""
            QPushButton {{
                background-color: transparent;
                color: {CLOUDY_SKY};
                border: 2px solid {PERSIAN_BLUE};
                border-radius: 12px;
                padding: 12px 15px 12px 22px;
                font-size: 12px;
                letter-spacing: 1.5px;
                text-align: left;
            }}
            QPushButton:hover {{
                border: 2px solid {self.glow_color.name()};
            }}
            QPushButton:pressed {{
                background-color: rgba(255,255,255,10);
            }}
        """
        self._active_style = f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 18);
                color: #ffffff;
                border: 2px solid {self.glow_color.name()};
                border-radius: 12px;
                padding: 12px 15px 12px 22px;
                font-size: 12px;
                letter-spacing: 1.5px;
                text-align: left;
            }}
        """
        self.setStyleSheet(self._base_style)

    def set_active(self, active: bool):
        self.is_active = active
        self.setStyleSheet(self._active_style if active else self._base_style)


# ---------------------------------------------------------------------------
# STAT CARD
# ---------------------------------------------------------------------------
class StatCard(QFrame):
    def __init__(self, title, value, accent_hex, orbitron, mono, subtitle=""):
        super().__init__()
        self.setObjectName("StatCard")
        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(6)

        title_lbl = QLabel(title.upper())
        title_lbl.setFont(QFont(orbitron, 9, QFont.Weight.Bold))
        title_lbl.setStyleSheet(
            f"color: {TEXT_MUTED}; letter-spacing: 2px; border: none; background: transparent;"
        )

        # Stored as an instance variable (self.) so it can be updated later
        self.value_lbl = QLabel(value)
        self.value_lbl.setFont(QFont(orbitron, 22, QFont.Weight.Black))
        self.value_lbl.setStyleSheet(f"color: {accent_hex}; border: none; background: transparent;")

        layout.addWidget(title_lbl)
        layout.addWidget(self.value_lbl)

        if subtitle:
            self.sub_lbl = QLabel(subtitle)
            self.sub_lbl.setFont(QFont(mono, 9))
            self.sub_lbl.setStyleSheet(f"color: {TEXT_MUTED}; border: none; background: transparent;")
            layout.addWidget(self.sub_lbl)

        layout.addStretch()

        self.setStyleSheet(f"""
            QFrame#StatCard {{
                background-color: {BG_CARD};
                border: 1px solid {TRUE_AZURE};
                border-radius: 16px;
            }}
        """)

    def update_value(self, new_val):
        """Dynamically updates the glowing number on the card."""
        self.value_lbl.setText(str(new_val))
        
    def update_subtitle(self, new_sub):
        """Optional: dynamically update the subtitle text if needed."""
        if hasattr(self, 'sub_lbl'):
            self.sub_lbl.setText(str(new_sub))


# ---------------------------------------------------------------------------
# GRID BACKGROUND  (faint 40px grid painted behind the stacked pages)
# ---------------------------------------------------------------------------
class GridBackground(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        line_color = QColor(VIVID_ROYAL)
        line_color.setAlpha(24)
        painter.setPen(line_color)
        step = 40
        for x in range(0, self.width(), step):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), step):
            painter.drawLine(0, y, self.width(), y)


# ---------------------------------------------------------------------------
# SCANLINES OVERLAY  (CRT-style horizontal lines, click-through, sits on
# top of the whole window)
# ---------------------------------------------------------------------------
class ScanlineOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        color = QColor(0, 0, 0, 15)
        painter.setPen(color)
        for y in range(0, self.height(), 4):
            painter.drawLine(0, y, self.width(), y)


# ---------------------------------------------------------------------------
# ROI GRAPHICS VIEW (Interactive Image Canvas)
# ---------------------------------------------------------------------------
class ROIGraphicsView(QGraphicsView):
    """
    Precision exam-sheet canvas.
    - PAN mode by default (drag to move, no accidental drawing). Explicit
      buttons drive zoom/rotate; mouse wheel is disabled so it never fights
      touchpad scrolling.
    - DRAW mode is entered on demand; finishing a box auto-reverts to PAN.
    - Rotation is baked into the pixmap (not just view-transformed) so a
      cropped save always matches what's on screen.
    - SmoothPixmapTransform render hint keeps zoomed-in image quality crisp.
    """
    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        self.setStyleSheet(f"""
            QGraphicsView {{
                background-color: {BG_CARD};
                border: 2px solid {TRUE_AZURE};
                border-radius: 12px;
            }}
            QScrollBar:horizontal {{
                border: none; background: {BG_PANEL}; height: 14px; margin: 0px;
            }}
            QScrollBar::handle:horizontal {{
                background: {PERSIAN_BLUE}; min-width: 20px; border-radius: 7px;
            }}
            QScrollBar:vertical {{
                border: none; background: {BG_PANEL}; width: 14px; margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {PERSIAN_BLUE}; min-height: 20px; border-radius: 7px;
            }}
        """)

        self.original_pixmap = None   # as-loaded (EXIF-corrected), rotation = 0
        self.current_pixmap = None    # original with rotation baked in
        self.image_item = None
        self.roi_rect = None
        self.start_pos = None
        self.rotation = 0
        self.current_mode = "PAN"
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    # -- mode -----------------------------------------------------------
    def set_mode(self, mode):
        self.current_mode = mode
        self.setDragMode(
            QGraphicsView.DragMode.NoDrag if mode == "DRAW" else QGraphicsView.DragMode.ScrollHandDrag
        )

    # -- loading / orientation -------------------------------------------
    def load_image(self, pixmap):
        self.original_pixmap = pixmap
        self.rotation = 0
        self._apply_pixmap(pixmap)
        self.set_mode("PAN")

    def _apply_pixmap(self, pixmap):
        self.current_pixmap = pixmap
        self.scene.clear()
        self.image_item = self.scene.addPixmap(pixmap)
        self.image_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.roi_rect = None
        self.start_pos = None
        self.setSceneRect(self.image_item.boundingRect())
        self.resetTransform()
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def rotate_image(self):
        """Bakes a 90° clockwise rotation into the pixmap so crop coords stay valid."""
        if self.original_pixmap is None:
            return
        self.rotation = (self.rotation + 90) % 360
        transform = QTransform().rotate(self.rotation)
        rotated = self.original_pixmap.transformed(transform, Qt.TransformationMode.SmoothTransformation)
        self._apply_pixmap(rotated)
        self.set_mode("PAN")

    # -- zoom (buttons only — wheel is disabled below) --------------------
    def zoom_in(self):
        self.scale(1.2, 1.2)

    def zoom_out(self):
        self.scale(1.0 / 1.2, 1.0 / 1.2)

    def wheelEvent(self, event):
        pass  # mouse/touchpad zoom intentionally disabled — use the buttons

    # -- ROI drawing --------------------------------------------------------
    def mousePressEvent(self, event):
        if self.current_mode == "DRAW" and self.image_item:
            self.start_pos = self.mapToScene(event.position().toPoint())
            if self.roi_rect:
                self.scene.removeItem(self.roi_rect)
            pen = QPen(QColor(NEON_PINK))
            pen.setWidth(3)
            pen.setCosmetic(True)  # constant on-screen thickness regardless of zoom
            brush = QBrush(QColor(247, 37, 133, 40))
            self.roi_rect = self.scene.addRect(QRectF(self.start_pos, self.start_pos), pen, brush)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.current_mode == "DRAW" and self.start_pos and self.roi_rect:
            current_pos = self.mapToScene(event.position().toPoint())
            rect = QRectF(self.start_pos, current_pos).normalized()
            self.roi_rect.setRect(rect)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.current_mode == "DRAW":
            self.start_pos = None
            self.set_mode("PAN")  # auto-revert so a second click can't redraw by accident
        else:
            super().mouseReleaseEvent(event)

    def clear_roi(self):
        """Erases the current bounding box without removing the image."""
        if self.roi_rect:
            self.scene.removeItem(self.roi_rect)
            self.roi_rect = None

    def get_roi_coordinates(self):
        if not self.roi_rect:
            return None
        rect = self.roi_rect.rect()
        return {
            "x": int(rect.x()),
            "y": int(rect.y()),
            "w": int(rect.width()),
            "h": int(rect.height()),
        }


# ---------------------------------------------------------------------------
# MAIN WINDOW
# ---------------------------------------------------------------------------
class LoginScreen(QWidget):
    def __init__(self, auth_callback, orbitron, mono):
        super().__init__()
        self.auth_callback = auth_callback

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(20)

        brand_lbl = QLabel("NEXUS EDGE")
        brand_lbl.setFont(QFont(orbitron, 36, QFont.Weight.Black))
        brand_lbl.setStyleSheet(f"color: {SKY_AQUA}; letter-spacing: 5px;")
        brand_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub_lbl = QLabel("SECURE ADMIN TERMINAL")
        sub_lbl.setFont(QFont(mono, 12))
        sub_lbl.setStyleSheet(f"color: {TEXT_MUTED}; letter-spacing: 2px;")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Enter Admin Password...")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setFont(QFont(orbitron, 14))
        self.password_input.setFixedWidth(400)
        self.password_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {BG_PANEL}; color: {NEON_PINK};
                border: 2px solid {TRUE_AZURE}; border-radius: 8px; padding: 15px;
            }}
            QLineEdit:focus {{ border: 2px solid {NEON_PINK}; }}
        """)
        self.password_input.returnPressed.connect(self.attempt_login)

        btn_login = QPushButton("AUTHENTICATE")
        btn_login.setFont(QFont(orbitron, 12, QFont.Weight.Bold))
        btn_login.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_login.setFixedWidth(400)
        btn_login.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {SKY_AQUA};
                border: 2px solid {SKY_AQUA}; border-radius: 8px; padding: 15px;
            }}
            QPushButton:hover {{ background-color: {SKY_AQUA}; color: #000000; }}
        """)
        btn_login.clicked.connect(self.attempt_login)

        self.error_lbl = QLabel("")
        self.error_lbl.setFont(QFont(mono, 10))
        self.error_lbl.setStyleSheet(f"color: {WARN_COLOR};")
        self.error_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        layout.addWidget(brand_lbl)
        layout.addWidget(sub_lbl)
        layout.addSpacing(40)
        layout.addWidget(self.password_input, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(btn_login, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.error_lbl)
        layout.addStretch()

    def attempt_login(self):
        if self.password_input.text() == "admin123":
            self.error_lbl.setText("")
            self.auth_callback()
        else:
            self.error_lbl.setText("ACCESS DENIED: Invalid Credentials")
            self.password_input.clear()


class WelcomeScreen(QWidget):
    def __init__(self, new_proj_cb, open_proj_cb, orbitron, mono):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(30)

        title = QLabel("WORKSPACE HUB")
        title.setFont(QFont(orbitron, 28, QFont.Weight.Black))
        title.setStyleSheet(f"color: {CLOUDY_SKY}; letter-spacing: 2px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(20)

        btn_new = QPushButton("+ NEW EXAM WORKSPACE")
        btn_new.setFont(QFont(orbitron, 12, QFont.Weight.Bold))
        btn_new.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_new.setFixedSize(300, 100)
        btn_new.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD}; color: {NEON_PINK};
                border: 2px solid {NEON_PINK}; border-radius: 12px;
            }}
            QPushButton:hover {{ background-color: {NEON_PINK}; color: #ffffff; }}
        """)
        btn_new.clicked.connect(new_proj_cb)

        btn_open = QPushButton("📂 OPEN EXISTING WORKSPACE")
        btn_open.setFont(QFont(orbitron, 12, QFont.Weight.Bold))
        btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open.setFixedSize(300, 100)
        btn_open.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_CARD}; color: {CLOUDY_SKY};
                border: 2px solid {CLOUDY_SKY}; border-radius: 12px;
            }}
            QPushButton:hover {{ background-color: {CLOUDY_SKY}; color: #000000; }}
        """)
        btn_open.clicked.connect(open_proj_cb)

        btn_row.addStretch()
        btn_row.addWidget(btn_new)
        btn_row.addWidget(btn_open)
        btn_row.addStretch()

        layout.addStretch()
        layout.addWidget(title)
        layout.addLayout(btn_row)
        layout.addStretch()


# ---------------------------------------------------------------------------
# PHASE 5: BACKGROUND WEBSOCKET WORKER THREAD
# ---------------------------------------------------------------------------
class ServerWorker(QThread):
    log_signal = Signal(str)

    def __init__(self, packet_data):
        super().__init__()
        self.packet_data = packet_data

    def run(self):
        try:
            import socket
            import json

            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind(('0.0.0.0', 8765))
            server_socket.listen(5)

            self.log_signal.emit("SERVER ONLINE: Listening on port 8765...")

            while True:
                client_sock, addr = server_socket.accept()
                self.log_signal.emit(f"CONNECTION DETECTED: Mobile Client connected from {addr[0]}")

                payload = json.dumps(self.packet_data).encode('utf-8')
                client_sock.sendall(payload + b"\n")
                client_sock.close()
                self.log_signal.emit("PACKET TRANSMITTED: Exam blueprint & roster synced to client.")

        except Exception as e:
            self.log_signal.emit(f"Server Error: {str(e)}")


class CyberpunkDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Nexus Edge Grading System")
        self.resize(1300, 860)

        # Global Project State
        self.active_project_dir = None
        self.active_project_name = None

        self._page_animations = []

        self.orbitron = resolve_font("Orbitron", "Segoe UI")
        self.rajdhani = resolve_font("Rajdhani", "Segoe UI")
        self.mono = resolve_font("JetBrains Mono", "Consolas")

        # Central widget holds the MASTER STACK: Login -> Welcome -> Dashboard
        central = QWidget()
        self.setCentralWidget(central)
        self.master_layout = QVBoxLayout(central)
        self.master_layout.setContentsMargins(0, 0, 0, 0)
        self.master_layout.setSpacing(0)

        self.master_stack = QStackedWidget()
        self.master_layout.addWidget(self.master_stack)

        self.login_screen = LoginScreen(self.handle_login_success, self.orbitron, self.mono)
        self.welcome_screen = WelcomeScreen(self.create_new_project, self.open_existing_project, self.orbitron, self.mono)
        self.main_dashboard_widget = self.build_main_dashboard()

        self.master_stack.addWidget(self.login_screen)          # Index 0
        self.master_stack.addWidget(self.welcome_screen)        # Index 1
        self.master_stack.addWidget(self.main_dashboard_widget)  # Index 2

        self.apply_stylesheet()
        self.master_stack.setCurrentIndex(0)

        # scanline overlay sits on top of everything, click-through
        self.scanlines = ScanlineOverlay(central)
        self.scanlines.setGeometry(central.rect())
        self.scanlines.raise_()

    def handle_login_success(self):
        self.master_stack.setCurrentIndex(1)

    # --- WORKSPACE MANAGER ---
    def create_new_project(self):
        proj_name, ok = QInputDialog.getText(self, "New Workspace", "Enter Exam/Project Name (e.g., Quiz 67):")
        if ok and proj_name:
            dir_path = QFileDialog.getExistingDirectory(self, "Select Directory to Save Project")
            if dir_path:
                self.active_project_name = proj_name
                self.active_project_dir = os.path.join(dir_path, proj_name.replace(" ", "_"))
                os.makedirs(self.active_project_dir, exist_ok=True)

                # Initialize empty project file
                config_path = os.path.join(self.active_project_dir, "nexus_project.json")
                with open(config_path, 'w') as f:
                    json.dump({"project_name": proj_name, "mode": "Quiz Mode", "mcq_count": 10}, f)

                self.activate_dashboard()

    def open_existing_project(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Existing Workspace Folder")
        if dir_path:
            config_path = os.path.join(dir_path, "nexus_project.json")
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    data = json.load(f)
                self.active_project_name = data.get("project_name", "Unknown Exam")
                self.active_project_dir = dir_path
                self.activate_dashboard()
            else:
                QMessageBox.critical(self, "Error", "Invalid Workspace: nexus_project.json not found in this folder.")

    def activate_dashboard(self):
        self.master_stack.setCurrentIndex(2)
        self.set_active_page(0)
        # Update the Topbar to show the locked context
        self.sub_brand.setText(f"// WORKSPACE: {self.active_project_name.upper()}")

    def build_main_dashboard(self):
        """Wraps the existing Topbar / Sidebar / Content area into one widget."""
        dashboard_container = QWidget()
        layout = QVBoxLayout(dashboard_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self.build_topbar())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        body_layout.addWidget(self.build_sidebar())
        body_layout.addWidget(self.build_content_area())

        layout.addWidget(body)
        return dashboard_container

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "scanlines"):
            self.scanlines.setGeometry(self.centralWidget().rect())
            self.scanlines.raise_()

    # ------------------------------------------------------------------
    # TOP BAR
    # ------------------------------------------------------------------
    def build_topbar(self):
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(64)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(28, 0, 28, 0)
        layout.setSpacing(14)

        self.brand_label = QLabel("NEXUS EDGE")
        self.brand_label.setFont(QFont(self.orbitron, 15, QFont.Weight.Black))
        self.brand_label.setStyleSheet(
            f"color: {SKY_AQUA}; letter-spacing: 3px; background: transparent; border: none;"
        )

        self.sub_brand = QLabel("// GRADING SYSTEM")
        self.sub_brand.setFont(QFont(self.orbitron, 9, QFont.Weight.DemiBold))
        self.sub_brand.setStyleSheet(
            f"color: {TEXT_MUTED}; letter-spacing: 3px; background: transparent; border: none;"
        )

        layout.addWidget(self.brand_label)
        layout.addWidget(self.sub_brand)
        layout.addStretch()

        status_dot = PulsingDot(SKY_AQUA, 10)
        status_lbl = QLabel("SYSTEM ONLINE")
        status_lbl.setFont(QFont(self.orbitron, 9, QFont.Weight.Bold))
        status_lbl.setStyleSheet(
            f"color: {SKY_AQUA}; letter-spacing: 1px; background: transparent; border: none;"
        )

        self.clock_lbl = QLabel()
        self.clock_lbl.setFont(QFont(self.mono, 10))
        self.clock_lbl.setStyleSheet(
            f"color: {TEXT_MUTED}; background: transparent; border: none; margin-left: 18px;"
        )
        self.update_clock()
        timer = QTimer(self)
        timer.timeout.connect(self.update_clock)
        timer.start(1000)

        layout.addWidget(status_dot)
        layout.addWidget(status_lbl)
        layout.addWidget(self.clock_lbl)

        return bar

    def update_clock(self):
        self.clock_lbl.setText(datetime.now().strftime("%H:%M:%S"))

    # ------------------------------------------------------------------
    # SIDEBAR
    # ------------------------------------------------------------------
    def build_sidebar(self):
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(256)

        self.sidebar_layout = QVBoxLayout(self.sidebar)
        self.sidebar_layout.setContentsMargins(20, 32, 20, 22)
        self.sidebar_layout.setSpacing(12)

        self.title_label = QLabel("NEURAL DECK")
        self.title_label.setObjectName("AppTitle")
        self.title_label.setFont(QFont(self.orbitron, 16, QFont.Weight.Black))
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sidebar_layout.addWidget(self.title_label)
        self.sidebar_layout.addSpacing(14)

        self.btn_setup = NavButton("⚙  Exam Blueprint", CLOUDY_SKY, self.orbitron)
        self.btn_templates = NavButton("▦  Template Builder", RASPBERRY_PLUM, self.orbitron)
        self.btn_live = NavButton("●  Session Manager", NEON_PINK, self.orbitron)

        self.nav_buttons = [self.btn_setup, self.btn_templates, self.btn_live]

        self.sidebar_layout.addWidget(self.btn_setup)
        self.sidebar_layout.addWidget(self.btn_templates)
        self.sidebar_layout.addWidget(self.btn_live)
        self.sidebar_layout.addStretch()

        footer = QLabel("v2.4.1 · SECURE LINK")
        footer.setFont(QFont(self.mono, 9))
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet(
            f"color: {TEXT_MUTED}; letter-spacing: 1px; background: transparent; border: none;"
        )
        self.sidebar_layout.addWidget(footer)

        self.indicator = QFrame(self.sidebar)
        self.indicator.setFixedWidth(4)
        self.indicator.setStyleSheet(f"background-color: {CLOUDY_SKY}; border-radius: 2px;")

        self.btn_setup.clicked.connect(lambda: self.set_active_page(0))
        self.btn_templates.clicked.connect(lambda: self.set_active_page(1))
        self.btn_live.clicked.connect(lambda: self.set_active_page(2))

        return self.sidebar

    def move_indicator(self, button: NavButton):
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

    # ------------------------------------------------------------------
    # CONTENT AREA  (grid background behind, stacked pages on top)
    # ------------------------------------------------------------------
    def build_content_area(self):
        container = QWidget()
        container.setObjectName("ContentContainer")
        stack_layout = QStackedLayout(container)
        stack_layout.setStackingMode(QStackedLayout.StackingMode.StackAll)

        self.grid_bg = GridBackground()
        stack_layout.addWidget(self.grid_bg)

        self.content_area = QStackedWidget()
        self.content_area.setObjectName("ContentArea")

        self.page_setup = self.build_setup_page()
        self.page_templates = self.build_templates_page()
        self.page_live = self.build_live_page()

        self.content_area.addWidget(self.page_setup)
        self.content_area.addWidget(self.page_templates)
        self.content_area.addWidget(self.page_live)

        stack_layout.addWidget(self.content_area)
        stack_layout.setCurrentWidget(self.content_area)

        return container

    def build_page_shell(self, heading, accent_hex, extra_header=None):
        """
        Builds the header (title + optional badge) for a page and returns
        the page widget along with its content QVBoxLayout so real widgets
        can be wired in below the header. Stat cards / activity feed were
        removed here — this is where live data should be attached, e.g.:

            page, content_layout = self.build_page_shell(...)
            content_layout.addWidget(my_real_stats_widget)
        """
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 30, 36, 30)
        layout.setSpacing(22)

        header_row = QHBoxLayout()
        heading_lbl = QLabel(heading)
        heading_lbl.setFont(QFont(self.orbitron, 20, QFont.Weight.Black))
        heading_lbl.setStyleSheet(
            f"color: {accent_hex}; letter-spacing: 1px; background: transparent; border: none;"
        )
        header_row.addWidget(heading_lbl)
        header_row.addStretch()
        if extra_header:
            header_row.addWidget(extra_header)
        layout.addLayout(header_row)

        layout.addStretch()

        return page, layout

    def build_setup_page(self):
        page, content_layout = self.build_page_shell("Exam Blueprint Configuration", CLOUDY_SKY)

        grid = QGridLayout()
        grid.setSpacing(20)

        input_style = f"""
            QWidget {{
                background-color: {BG_PANEL}; color: {SKY_AQUA};
                border: 2px solid {PERSIAN_BLUE}; border-radius: 8px; padding: 10px;
                font-size: 14px; font-weight: bold;
            }}
            QWidget:focus {{ border: 2px solid {NEON_PINK}; }}
        """

        self.exam_mode_combo = QComboBox()
        self.exam_mode_combo.addItems(["Quiz Mode", "Shamel Mode"])
        self.exam_mode_combo.setStyleSheet(input_style)

        self.mcq_count_spin = QSpinBox()
        self.mcq_count_spin.setRange(1, 150)
        self.mcq_count_spin.setValue(10)
        self.mcq_count_spin.setStyleSheet(input_style)

        self.mcq_points_spin = QDoubleSpinBox()
        self.mcq_points_spin.setRange(0.5, 100.0)
        self.mcq_points_spin.setValue(1.0)
        self.mcq_points_spin.setSingleStep(0.5)
        self.mcq_points_spin.setStyleSheet(input_style)

        grid.addWidget(QLabel("EXAM MODE:"), 0, 0)
        grid.addWidget(self.exam_mode_combo, 0, 1)
        grid.addWidget(QLabel("TOTAL MCQ:"), 1, 0)
        grid.addWidget(self.mcq_count_spin, 1, 1)
        grid.addWidget(QLabel("POINTS PER MCQ:"), 1, 2)
        grid.addWidget(self.mcq_points_spin, 1, 3)

        # -- Granular Essay Section --
        self.essay_checkbox = QCheckBox(" INCLUDE WRITTEN / ESSAY QUESTIONS")
        self.essay_checkbox.setFont(QFont(self.orbitron, 11, QFont.Weight.Bold))
        self.essay_checkbox.setStyleSheet(f"color: {NEON_PINK};")

        self.essay_count_spin = QSpinBox()
        self.essay_count_spin.setRange(1, 20)
        self.essay_count_spin.setStyleSheet(input_style)
        self.essay_count_spin.setEnabled(False)
        self.essay_count_spin.valueChanged.connect(self.generate_essay_inputs)

        grid.addWidget(self.essay_checkbox, 2, 0, 1, 2)
        grid.addWidget(QLabel("ESSAY COUNT:"), 2, 2)
        grid.addWidget(self.essay_count_spin, 2, 3)

        # Scroll area for granular essay points
        self.essay_scroll = QScrollArea()
        self.essay_scroll.setWidgetResizable(True)
        self.essay_scroll.setStyleSheet("border: none; background: transparent;")
        self.essay_container = QWidget()
        self.essay_container.setStyleSheet("background: transparent;")
        self.essay_layout = QGridLayout(self.essay_container)
        self.essay_scroll.setWidget(self.essay_container)

        self.essay_checkbox.toggled.connect(self.toggle_essay_inputs)
        self.essay_spinboxes = []  # Keeps track of our individual inputs

        content_layout.addLayout(grid)
        content_layout.addWidget(self.essay_scroll)

        btn_save_blueprint = QPushButton("💾 SAVE EXAM BLUEPRINT")
        btn_save_blueprint.setFont(QFont(self.orbitron, 12, QFont.Weight.Bold))
        btn_save_blueprint.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_blueprint.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {CLOUDY_SKY};
                border: 2px solid {CLOUDY_SKY}; border-radius: 8px; padding: 15px; margin-top: 10px;
            }}
            QPushButton:hover {{ background-color: {CLOUDY_SKY}; color: #000000; }}
        """)
        btn_save_blueprint.clicked.connect(self.save_blueprint_to_project)

        content_layout.addWidget(btn_save_blueprint)

        for i in range(grid.count()):
            widget = grid.itemAt(i).widget()
            if isinstance(widget, QLabel):
                widget.setFont(QFont(self.orbitron, 10, QFont.Weight.Bold))
                widget.setStyleSheet(f"color: {TEXT_MUTED};")
        return page

    def toggle_essay_inputs(self, checked):
        self.essay_count_spin.setEnabled(checked)
        if checked:
            self.generate_essay_inputs()
        else:
            self.clear_essay_inputs()

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

        count = self.essay_count_spin.value()
        for i in range(count):
            lbl = QLabel(f"Essay Q{i+1} Points:")
            lbl.setFont(QFont(self.orbitron, 10, QFont.Weight.Bold))
            lbl.setStyleSheet(f"color: {TEXT_MUTED};")

            spin = QDoubleSpinBox()
            spin.setRange(0.5, 50.0)
            spin.setValue(2.0)
            spin.setStyleSheet(f"background-color: {BG_PANEL}; color: {NEON_PINK}; border: 1px solid {PERSIAN_BLUE}; border-radius: 4px; padding: 5px;")

            row = i // 3
            col = (i % 3) * 2
            self.essay_layout.addWidget(lbl, row, col)
            self.essay_layout.addWidget(spin, row, col + 1)
            self.essay_spinboxes.append(spin)

    def save_blueprint_to_project(self):
        if not self.active_project_dir:
            return

        has_essays = self.essay_checkbox.isChecked()
        essay_data = {}
        if has_essays:
            for i, spin in enumerate(self.essay_spinboxes):
                essay_data[f"Q{i+1}"] = spin.value()

        config = {
            "project_name": self.active_project_name,
            "mode": self.exam_mode_combo.currentText(),
            "mcq_count": self.mcq_count_spin.value(),
            "mcq_points": self.mcq_points_spin.value(),
            "has_essays": has_essays,
            "essay_points_map": essay_data
        }

        config_path = os.path.join(self.active_project_dir, "nexus_project.json")
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=4)

        QMessageBox.information(self, "Success", f"Blueprint saved to project workspace:\n{self.active_project_name}")

    def _tool_button(self, text, color):
        btn = QPushButton(text)
        btn.setFont(QFont(self.orbitron, 9, QFont.Weight.Bold))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {color};
                border: 2px solid {color}; border-radius: 6px; padding: 10px;
            }}
            QPushButton:hover {{ background-color: {color}; color: #000000; }}
        """)
        return btn

    def build_templates_page(self):
        page, content_layout = self.build_page_shell("ROI Template Builder", RASPBERRY_PLUM)

        # -- 1. Precision Toolbelt --
        tool_row = QHBoxLayout()
        tool_row.setSpacing(10)

        btn_load = self._tool_button("📷 1. LOAD IMAGE", CLOUDY_SKY)
        btn_pan = self._tool_button("🖐 PAN MODE", TEXT_MUTED)
        btn_rotate = self._tool_button("↻ ROTATE", SKY_AQUA)
        btn_zoom_in = self._tool_button("➕ ZOOM", ELECTRIC_SAPPHIRE)
        btn_zoom_out = self._tool_button("➖ ZOOM", ELECTRIC_SAPPHIRE)
        btn_draw = self._tool_button("🎯 2. DRAW ROI", NEON_PINK)
        btn_clear_roi = self._tool_button("↺ CLEAR ROI", WARN_COLOR)

        btn_load.clicked.connect(self.load_template_image)
        btn_pan.clicked.connect(lambda: self.roi_canvas.set_mode("PAN"))
        btn_rotate.clicked.connect(lambda: self.roi_canvas.rotate_image())
        btn_zoom_in.clicked.connect(lambda: self.roi_canvas.zoom_in())
        btn_zoom_out.clicked.connect(lambda: self.roi_canvas.zoom_out())
        btn_draw.clicked.connect(lambda: self.roi_canvas.set_mode("DRAW"))
        btn_clear_roi.clicked.connect(lambda: self.roi_canvas.clear_roi())

        for b in (btn_load, btn_pan, btn_rotate, btn_zoom_in, btn_zoom_out, btn_draw, btn_clear_roi):
            tool_row.addWidget(b)
        tool_row.addStretch()

        # -- 3. Canvas --
        self.roi_canvas = ROIGraphicsView()
        self.roi_canvas.setMinimumHeight(420)

        # -- 4. Save --
        self.btn_save_tpl = QPushButton("💾 3. CROP & SAVE TEMPLATE")
        self.btn_save_tpl.setFont(QFont(self.orbitron, 12, QFont.Weight.Bold))
        self.btn_save_tpl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_tpl.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {NEON_PINK};
                border: 2px solid {NEON_PINK}; border-radius: 8px; padding: 15px; margin-top: 10px;
            }}
            QPushButton:hover {{ background-color: {NEON_PINK}; color: #ffffff; }}
        """)
        self.btn_save_tpl.clicked.connect(self.save_template_session)

        content_layout.addLayout(tool_row)
        content_layout.addWidget(self.roi_canvas)
        content_layout.addWidget(self.btn_save_tpl)

        return page

    def load_template_image(self):
        options = QFileDialog.Option.DontUseNativeDialog
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Exam Image", "", "Images (*.png *.jpg *.jpeg)", options=options
        )
        if file_path:
            self.current_image_path = file_path
            # Auto-correct camera EXIF orientation so photos load upright
            reader = QImageReader(file_path)
            reader.setAutoTransform(True)
            image = reader.read()
            pixmap = QPixmap.fromImage(image)
            self.roi_canvas.load_image(pixmap)

    def save_template_session(self):
        if not self.active_project_dir:
            QMessageBox.warning(self, "Error", "No active workspace. Open or create a project first.")
            return

        roi_coords = self.roi_canvas.get_roi_coordinates()
        if not roi_coords:
            QMessageBox.warning(self, "Error", "You must draw a target ROI box first.")
            return

        try:
            # Crop the CURRENT pixmap (rotation baked in) to the drawn rectangle,
            # so the crop always matches what was seen on screen.
            saved_image_path = os.path.join(self.active_project_dir, "cropped_template.jpg")
            crop_rect = QRect(roi_coords['x'], roi_coords['y'], roi_coords['w'], roi_coords['h'])
            cropped_pixmap = self.roi_canvas.current_pixmap.copy(crop_rect)
            cropped_pixmap.save(saved_image_path, "JPG", 100)

            # Update the project JSON
            config_path = os.path.join(self.active_project_dir, "nexus_project.json")
            with open(config_path, 'r') as f:
                config = json.load(f)

            config["template_path"] = saved_image_path
            config["roi_coordinates"] = roi_coords

            with open(config_path, 'w') as f:
                json.dump(config, f, indent=4)

            QMessageBox.information(self, "Success", "Template cropped and saved to workspace!")

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ==================================================================
    # PHASE 4 & 5: SESSION MANAGER (HUB, DETAIL, MONITOR)
    # ==================================================================
    def build_live_page(self):
        """Creates an internal Stacked Widget to handle the 3 sub-screens of Session Management."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.session_sub_stack = QStackedWidget()
        layout.addWidget(self.session_sub_stack)

        self.active_group_name = None  # Tracks which group card is currently open

        # Build the 3 sub-screens
        self.page_group_hub = self.build_group_hub_ui()
        self.page_group_detail = self.build_group_detail_ui()
        self.page_monitoring = self.build_monitoring_ui()

        self.session_sub_stack.addWidget(self.page_group_hub)     # Index 0
        self.session_sub_stack.addWidget(self.page_group_detail)  # Index 1
        self.session_sub_stack.addWidget(self.page_monitoring)    # Index 2

        return container

    # --- SCREEN 1: GROUP HUB ---
    def build_group_hub_ui(self):
        page, content_layout = self.build_page_shell("Groups & Rosters Hub", NEON_PINK)

        btn_add_group = QPushButton("+ ADD NEW GROUP")
        btn_add_group.setFont(QFont(self.orbitron, 12, QFont.Weight.Bold))
        btn_add_group.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_group.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PANEL}; color: {NEON_PINK};
                border: 2px dashed {NEON_PINK}; border-radius: 12px; padding: 20px;
            }}
            QPushButton:hover {{ background-color: {NEON_PINK}; color: #ffffff; border-style: solid; }}
        """)
        btn_add_group.clicked.connect(self.create_new_group)
        content_layout.addWidget(btn_add_group)

        # Scroll area for Group Cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        self.groups_container = QWidget()
        self.groups_container.setStyleSheet("background: transparent;")
        self.groups_layout = QGridLayout(self.groups_container)
        self.groups_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.groups_container)

        content_layout.addWidget(scroll)
        return page

    def refresh_group_hub(self):
        """Reads DB and draws a card for each existing group."""
        if not self.active_project_dir:
            return

        # Clear existing cards
        for i in reversed(range(self.groups_layout.count())):
            widget = self.groups_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        db_path = os.path.join(self.active_project_dir, "roster.db")
        if not os.path.exists(db_path):
            return

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # Create table if not exists just in case
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS students (
                    student_id TEXT PRIMARY KEY, student_name TEXT NOT NULL,
                    is_present INTEGER DEFAULT 1, group_name TEXT
                )
            """)

            cursor.execute("SELECT group_name, COUNT(*) FROM students GROUP BY group_name")
            groups = cursor.fetchall()
            conn.close()

            row, col = 0, 0
            for group_name, count in groups:
                if not group_name:
                    continue

                card = QPushButton()
                card.setCursor(Qt.CursorShape.PointingHandCursor)
                card.setFixedSize(300, 120)
                card.setStyleSheet(f"""
                    QPushButton {{
                        background-color: {BG_CARD}; color: {SKY_AQUA};
                        border: 2px solid {TRUE_AZURE}; border-radius: 12px; text-align: left; padding: 15px;
                    }}
                    QPushButton:hover {{ border: 2px solid {CLOUDY_SKY}; background-color: rgba(72, 149, 239, 0.1); }}
                """)

                # Internal layout for the card
                card_layout = QVBoxLayout(card)
                title = QLabel(group_name.upper())
                title.setFont(QFont(self.orbitron, 14, QFont.Weight.Bold))
                title.setStyleSheet(f"color: {SKY_AQUA}; background: transparent; border: none;")

                sub = QLabel(f"{count} Students Enrolled")
                sub.setFont(QFont(self.mono, 10))
                sub.setStyleSheet(f"color: {TEXT_MUTED}; background: transparent; border: none;")

                card_layout.addWidget(title)
                card_layout.addWidget(sub)

                # Capture the current group_name in the lambda
                card.clicked.connect(lambda checked, g=group_name: self.open_group_detail(g))

                self.groups_layout.addWidget(card, row, col)
                col += 1
                if col > 2:
                    col = 0
                    row += 1

        except Exception as e:
            print(f"Error loading groups: {e}")

    def create_new_group(self):
        if not self.active_project_dir:
            QMessageBox.warning(self, "Error", "No active workspace. Create/Open a project first.")
            return

        group_name, ok = QInputDialog.getText(self, "New Group", "Enter Group Identifier (e.g., Sidi Bishr 10 AM):")
        if ok and group_name.strip():
            self.open_group_detail(group_name.strip())

    # --- SCREEN 2: GROUP DETAIL ---
    def build_group_detail_ui(self):
        page, content_layout = self.build_page_shell("Group Management", CLOUDY_SKY)

        # Header Row with Back Button
        header_row = QHBoxLayout()
        btn_back = QPushButton("◀ BACK TO GROUPS")
        btn_back.setFont(QFont(self.orbitron, 10, QFont.Weight.Bold))
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.setStyleSheet(f"QPushButton {{ background-color: transparent; color: {TEXT_MUTED}; border: none; }} QPushButton:hover {{ color: #ffffff; }}")
        btn_back.clicked.connect(lambda: self.session_sub_stack.setCurrentIndex(0))

        self.detail_group_title = QLabel("GROUP_NAME")
        self.detail_group_title.setFont(QFont(self.orbitron, 16, QFont.Weight.Black))
        self.detail_group_title.setStyleSheet(f"color: {SKY_AQUA};")

        header_row.addWidget(btn_back)
        header_row.addStretch()
        header_row.addWidget(self.detail_group_title)
        content_layout.addLayout(header_row)

        # Excel Import Section
        roster_row = QHBoxLayout()
        btn_load_excel = QPushButton("📂 IMPORT EXCEL ROSTER(S)")
        btn_load_excel.setFont(QFont(self.orbitron, 10, QFont.Weight.Bold))
        btn_load_excel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_load_excel.setStyleSheet(f"QPushButton {{ background-color: {BG_PANEL}; color: {CLOUDY_SKY}; border: 2px solid {CLOUDY_SKY}; border-radius: 8px; padding: 15px; }} QPushButton:hover {{ background-color: {CLOUDY_SKY}; color: #000000; }}")
        btn_load_excel.clicked.connect(self.load_group_excel)

        self.loaded_files_list = QListWidget()
        self.loaded_files_list.setFixedHeight(100)
        self.loaded_files_list.setStyleSheet(f"QListWidget {{ background-color: {BG_DEEP}; color: {TEXT_MUTED}; border: 1px solid {VIVID_ROYAL}; border-radius: 8px; padding: 5px; }}")

        roster_row.addWidget(btn_load_excel)
        roster_row.addWidget(self.loaded_files_list)
        content_layout.addLayout(roster_row)

        self.card_session_students = StatCard("Group Roster", "0", SKY_AQUA, self.orbitron, self.mono, "Total loaded for this group")
        content_layout.addWidget(self.card_session_students)

        # Start Button
        self.btn_start_server = QPushButton("🚀 START LIVE GRADING")
        self.btn_start_server.setFont(QFont(self.orbitron, 16, QFont.Weight.Black))
        self.btn_start_server.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start_server.setFixedHeight(80)
        self.btn_start_server.setStyleSheet(f"QPushButton {{ background-color: {BG_PANEL}; color: {NEON_PINK}; border: 3px solid {NEON_PINK}; border-radius: 12px; letter-spacing: 2px; }} QPushButton:hover {{ background-color: {NEON_PINK}; color: #ffffff; }}")
        self.btn_start_server.clicked.connect(self.start_live_grading_session)
        content_layout.addWidget(self.btn_start_server)

        return page

    def open_group_detail(self, group_name):
        self.active_group_name = group_name
        self.detail_group_title.setText(group_name.upper())
        self.loaded_files_list.clear()

        # Fetch current student count for this specific group
        count = 0
        db_path = os.path.join(self.active_project_dir, "roster.db")
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT COUNT(*) FROM students WHERE group_name = ?", (group_name,))
                count = cursor.fetchone()[0]
            except Exception:
                pass
            conn.close()

        self.card_session_students.update_value(str(count))
        self.session_sub_stack.setCurrentIndex(1)  # Switch to detail view

    def load_group_excel(self):
        options = QFileDialog.Option.DontUseNativeDialog
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Group Roster Excel", "", "Excel Files (*.xlsx *.xls)", options=options)
        if not file_path:
            return

        try:
            import pandas as pd
            df = pd.read_excel(file_path)
            df.columns = [str(col).lower().strip() for col in df.columns]

            if 'id' not in df.columns or 'name' not in df.columns:
                QMessageBox.critical(self, "Error", "Excel file must contain 'id' and 'name' columns.")
                return

            df_clean = df.dropna(subset=['id']).copy()
            if 's6' in df_clean.columns:
                df_clean['is_present'] = df_clean['s6'].apply(lambda x: 1 if pd.notnull(x) and str(x).strip() == '1' else 0)
            else:
                df_clean['is_present'] = 1

            db_path = os.path.join(self.active_project_dir, "roster.db")
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS students (
                    student_id TEXT PRIMARY KEY, student_name TEXT NOT NULL,
                    is_present INTEGER DEFAULT 1, group_name TEXT
                )
            """)

            total_inserted = 0
            for _, row in df_clean.iterrows():
                student_id = str(row['id']).strip()
                student_name = str(row['name']).strip()
                is_present = int(row['is_present'])
                cursor.execute(
                    "INSERT OR REPLACE INTO students (student_id, student_name, is_present, group_name) VALUES (?, ?, ?, ?)",
                    (student_id, student_name, is_present, self.active_group_name)
                )
                total_inserted += 1

            conn.commit()
            cursor.execute("SELECT COUNT(*) FROM students WHERE group_name = ?", (self.active_group_name,))
            total_in_group = cursor.fetchone()[0]
            conn.close()

            self.card_session_students.update_value(str(total_in_group))
            self.loaded_files_list.addItem(f"✔ {os.path.basename(file_path)} (+{total_inserted} records)")

        except Exception as e:
            QMessageBox.critical(self, "Data Import Error", str(e))

    # --- SCREEN 3: LIVE MONITORING ---
    def build_monitoring_ui(self):
        page, content_layout = self.build_page_shell("Live Broadcast Monitor", NEON_PINK)

        self.monitor_log = QListWidget()
        self.monitor_log.setStyleSheet(f"QListWidget {{ background-color: {BG_DEEP}; color: {NEON_PINK}; border: 1px solid {TRUE_AZURE}; border-radius: 8px; padding: 15px; font-family: Consolas; font-size: 13px; }}")
        content_layout.addWidget(self.monitor_log)

        btn_row = QHBoxLayout()
        btn_stop = QPushButton("🛑 STOP SERVER")
        btn_stop.setFont(QFont(self.orbitron, 12, QFont.Weight.Bold))
        btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_stop.setStyleSheet(f"QPushButton {{ background-color: {WARN_COLOR}; color: #ffffff; border-radius: 8px; padding: 15px; }}")
        btn_stop.clicked.connect(self.stop_server_and_return)

        btn_export = QPushButton("📊 EXPORT RESULTS")
        btn_export.setFont(QFont(self.orbitron, 12, QFont.Weight.Bold))
        btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_export.setStyleSheet(f"QPushButton {{ background-color: {ELECTRIC_SAPPHIRE}; color: #ffffff; border-radius: 8px; padding: 15px; }}")
        btn_export.clicked.connect(self.export_group_results)

        btn_row.addWidget(btn_stop)
        btn_row.addWidget(btn_export)
        content_layout.addLayout(btn_row)

        return page

    def start_live_grading_session(self):
        try:
            config_path = os.path.join(self.active_project_dir, "nexus_project.json")
            if not os.path.exists(config_path):
                QMessageBox.critical(self, "Error", "No Exam Blueprint found.")
                return
            with open(config_path, 'r') as f:
                exam_config = json.load(f)

            db_path = os.path.join(self.active_project_dir, "roster.db")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT student_id, student_name, is_present FROM students WHERE group_name = ?", (self.active_group_name,))
            student_rows = cursor.fetchall()
            conn.close()

            if not student_rows:
                QMessageBox.critical(self, "Error", f"No roster found for '{self.active_group_name}'! Import Excel first.")
                return

            roster_list = [{"id": s["student_id"], "name": s["student_name"], "present": s["is_present"]} for s in student_rows]

            master_packet = {
                "exam_name": exam_config.get("project_name", self.active_project_name),
                "exam_mode": exam_config.get("mode", "Quiz Mode"),
                "mcq_count": exam_config.get("mcq_count", 0),
                "mcq_points": exam_config.get("mcq_points", 0),
                "has_essays": exam_config.get("has_essays", False),
                "essay_points_map": exam_config.get("essay_points_map", {}),
                "template_path": exam_config.get("template_path", ""),
                "roi_coordinates": exam_config.get("roi_coordinates", {}),
                "group_name": self.active_group_name,
                "roster": roster_list
            }

            # Start Thread
            self.server_thread = ServerWorker(master_packet)
            self.server_thread.log_signal.connect(self.log_server_message)
            self.server_thread.start()

            # Switch UI to Monitoring Mode
            self.monitor_log.clear()
            self.session_sub_stack.setCurrentIndex(2)

        except Exception as e:
            QMessageBox.critical(self, "Initialization Error", str(e))

    def log_server_message(self, message):
        self.monitor_log.addItem(message)
        self.monitor_log.scrollToItem(self.monitor_log.item(self.monitor_log.count() - 1))

    def stop_server_and_return(self):
        """Kills the socket thread and goes back to the Group Hub."""
        if hasattr(self, 'server_thread') and self.server_thread.isRunning():
            self.server_thread.terminate()  # forcefully stops the loop
            self.server_thread.wait()
            self.log_server_message("SERVER SHUTDOWN.")

        self.refresh_group_hub()
        self.session_sub_stack.setCurrentIndex(0)  # Return to Hub

    def export_group_results(self):
        """Exports the active group to an Excel file using Pandas."""
        if not self.active_group_name or not self.active_project_dir:
            return

        save_path, _ = QFileDialog.getSaveFileName(self, "Save Results As", f"{self.active_group_name}_Results.xlsx", "Excel Files (*.xlsx)")
        if not save_path:
            return

        try:
            import pandas as pd

            db_path = os.path.join(self.active_project_dir, "roster.db")
            conn = sqlite3.connect(db_path)
            df = pd.read_sql_query("SELECT student_id AS ID, student_name AS Name, is_present AS Status FROM students WHERE group_name = ?", conn, params=(self.active_group_name,))
            conn.close()

            # Map internal presence status to readable strings
            df['Status'] = df['Status'].apply(lambda x: 'Present' if x == 1 else 'Absent')

            df.to_excel(save_path, index=False)
            QMessageBox.information(self, "Success", f"Results successfully exported to:\n{save_path}")

        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))


    # ------------------------------------------------------------------
    # PAGE SWITCHING
    # ------------------------------------------------------------------
    def set_active_page(self, index):
        self.content_area.setCurrentIndex(index)

        # --- Refresh cards when entering the Session Manager ---
        if index == 2:
            self.session_sub_stack.setCurrentIndex(0)  # Reset to Hub
            self.refresh_group_hub()
        # ---------------------------------------------------------

        for i, btn in enumerate(self.nav_buttons):
            btn.set_active(i == index)

        active_btn = self.nav_buttons[index]
        QTimer.singleShot(0, lambda: self.move_indicator(active_btn))

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self.move_indicator(self.nav_buttons[0]))

    # ------------------------------------------------------------------
    # GLOBAL STYLESHEET
    # ------------------------------------------------------------------
    def apply_stylesheet(self):
        qss = f"""
        QMainWindow, QWidget#ContentContainer {{
            background-color: {BG_DEEP};
        }}

        QFrame#TopBar {{
            background-color: {BG_PANEL};
            border-bottom: 2px solid {VIVID_ROYAL};
        }}

        QFrame#Sidebar {{
            background-color: {BG_PANEL};
            border-right: 2px solid {VIVID_ROYAL};
        }}

        QLabel#AppTitle {{
            color: {SKY_AQUA};
            letter-spacing: 3px;
            background: transparent;
            border: none;
        }}
        """
        self.setStyleSheet(qss)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = CyberpunkDashboard()
    window.show()
    sys.exit(app.exec())