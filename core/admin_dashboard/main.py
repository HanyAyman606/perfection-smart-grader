"""
main.py
-------
"""

import sys
from PySide6.QtWidgets import QApplication, QMessageBox
from admin_dashboard.dashboard import OptiMarkDashboard
from admin_dashboard.licensing.license_manager import verify_license, LicenseError


def main():
    app = QApplication(sys.argv)

    # Checked before the main window is ever constructed — a compiled
    # build with no valid license.lic should get no further than this
    # dialog, regardless of what page/feature they try to reach.
    try:
        verify_license()
    except LicenseError as exc:
        QMessageBox.critical(None, "OPTIMARK — License Required", str(exc))
        sys.exit(1)

    window = OptiMarkDashboard()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

