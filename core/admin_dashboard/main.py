"""
main.py
-------
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
