"""
theme.py
--------
Single source of truth for colors and fonts. Every widget/page imports
from here instead of redefining hex strings, so a re-theme means editing
this one file, not hunting through a dozen files.

v2: switched from dark cyberpunk to a bright, light-background SaaS look
(Angular/React dashboard style) — vivid gradient accents on white cards.
Variable NAMES are unchanged from v1 on purpose, so every existing page/
dialog that imports e.g. NEON_PINK or BG_CARD just picks up the new value
automatically.
"""

from PySide6.QtGui import QFontDatabase

# ---------------------------------------------------------------------------
# PALETTE — vivid accent colors, kept in the same hue families as before
# (pink / fuchsia / violet / indigo / blue / sky / cyan) so the "meaning"
# of each name is still intuitive, just brightened for a light background.
# ---------------------------------------------------------------------------
NEON_PINK = "#ec4899"
RASPBERRY_PLUM = "#c026d3"
INDIGO_BLOOM = "#7c3aed"
ULTRASONIC_BLUE = "#6366f1"
TRUE_AZURE = "#3b82f6"
VIVID_ROYAL = "#4338ca"
PERSIAN_BLUE = "#2563eb"
ELECTRIC_SAPPHIRE = "#0ea5e9"
CLOUDY_SKY = "#38bdf8"
SKY_AQUA = "#06b6d4"

# ---------------------------------------------------------------------------
# SURFACES / TEXT — light theme
# ---------------------------------------------------------------------------
BG_DEEP = "#f1f5f9"     # overall app canvas (was near-black, now slate-100)
BG_PANEL = "#ffffff"    # topbar / sidebar / input backgrounds
BG_CARD = "#ffffff"     # cards — defined by colored border + shadow, not a dark fill
TEXT_MUTED = "#64748b"  # secondary/label text
TEXT_FEED = "#1e293b"   # primary readable text (was light-on-dark, now dark-on-light)
WARN_COLOR = "#dc2626"

# A couple of ready-made gradients for primary buttons/headers, matching the
# "gradient CTA button" look common in React/Angular dashboards. Qt's QSS
# does support qlineargradient(), so this drops straight into a stylesheet
# background-color.
GRADIENT_PRIMARY = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {NEON_PINK}, stop:1 {ULTRASONIC_BLUE})"
GRADIENT_COOL = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ELECTRIC_SAPPHIRE}, stop:1 {SKY_AQUA})"

# Shared QSS snippet used by every spinbox/combo/lineedit-style input across
# pages. Text is now dark-on-white instead of accent-on-dark for readability;
# the accent still shows up in the border + focus ring.
INPUT_STYLE = f"""
    QWidget {{
        background-color: {BG_PANEL}; color: {TEXT_FEED};
        border: 2px solid {TRUE_AZURE}; border-radius: 8px; padding: 10px;
        font-size: 14px; font-weight: bold;
    }}
    QWidget:focus {{ border: 2px solid {NEON_PINK}; }}

    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        background-color: {TRUE_AZURE};
        border: none;
        width: 18px;
        subcontrol-origin: border;
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{
        subcontrol-position: top right;
        border-top-right-radius: 6px;
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        subcontrol-position: bottom right;
        border-bottom-right-radius: 6px;
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
        background-color: {NEON_PINK};
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
        border-bottom: 5px solid #ffffff; width: 0; height: 0;
    }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
        border-top: 5px solid #ffffff; width: 0; height: 0;
    }}
"""


def resolve_font(preferred: str, fallback: str) -> str:
    """
    Orbitron / Rajdhani / JetBrains Mono are loaded from Google Fonts in the
    React version via @import url(...). Qt has no equivalent auto-fetch, so
    we check whether they're installed on the system and gracefully fall
    back to a system sans/mono if not. For pixel-perfect parity, install
    these three font families locally before running.
    """
    available = QFontDatabase.families()
    return preferred if preferred in available else fallback


class Fonts:
    """Resolved once and shared — avoids re-querying QFontDatabase everywhere."""
    def __init__(self):
        self.orbitron = resolve_font("Orbitron", "Segoe UI")
        self.rajdhani = resolve_font("Rajdhani", "Segoe UI")
        self.mono = resolve_font("JetBrains Mono", "Consolas")


def build_global_stylesheet() -> str:
    return f"""
    QMainWindow, QWidget#ContentContainer {{
        background-color: {BG_DEEP};
    }}

    QFrame#TopBar {{
        background-color: {BG_PANEL};
        border-bottom: 2px solid #e2e8f0;
    }}

    QFrame#Sidebar {{
        background-color: {BG_PANEL};
        border-right: 2px solid #e2e8f0;
    }}

    QLabel#AppTitle {{
        color: {ULTRASONIC_BLUE};
        letter-spacing: 3px;
        background: transparent;
        border: none;
    }}
    """