"""
main.py
-------
Entry point. Run with: python main.py
from the admin_dashboard package directory, or from the project root with
python core/admin_dashboard/main.py.
"""

from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from PySide6.QtWidgets import QApplication
from admin_dashboard.dashboard import CyberpunkDashboard


def main():
    app = QApplication(sys.argv)
    window = CyberpunkDashboard()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
