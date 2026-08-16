"""
main.py
-------
"""

import os
import sys

_this_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_this_dir)
sys.path[:] = [p for p in sys.path if os.path.abspath(p) != _this_dir]
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def main():
    if "--fingerprint" in sys.argv:
        from admin_dashboard.licensing.license_manager import machine_fingerprint
        print(machine_fingerprint())
        return

    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication(sys.argv)

    # Only import dashboard/group_registry/etc. AFTER QApplication exists —
    # these transitively construct QObject-based singletons (group_registry),
    # and creating QObjects before a QApplication is unsupported in
    # PySide6, causing "RuntimeError: Signal source has been deleted"
    # later when something tries to use their signals.
    from admin_dashboard.dashboard import OptiMarkDashboard
    from admin_dashboard.licensing.license_manager import verify_license, LicenseError

    try:
        verify_license()
    except LicenseError as exc:
        QMessageBox.critical(None, "Smart Grader — License Required", str(exc))
        sys.exit(1)

    window = OptiMarkDashboard()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()