"""
main.py
-------
Entry point. Run with: python -m admin_dashboard.main
(from the directory that CONTAINS admin_dashboard/, so the package
imports resolve).
"""

import sys
from PySide6.QtWidgets import QApplication

from admin_dashboard.dashboard import CyberpunkDashboard


def main():
    app = QApplication(sys.argv)
    window = CyberpunkDashboard()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
