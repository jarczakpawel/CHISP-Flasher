from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

APP_NAME = 'CHISP Flasher'
APP_ID = 'chisp-flasher'


def _app_icon_path() -> str:
    return str(Path(__file__).resolve().parents[1] / 'ui' / 'assets' / 'app_icon.png')


def _has_installed_linux_desktop_file() -> bool:
    desktop_name = f'{APP_ID}.desktop'
    for path in (
        Path.home() / '.local/share/applications' / desktop_name,
        Path('/usr/local/share/applications') / desktop_name,
        Path('/usr/share/applications') / desktop_name,
    ):
        if path.is_file():
            return True
    return False


def _apply_platform_metadata(app: QApplication) -> None:
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName('Paweł Jarczak')

    if sys.platform.startswith('linux') and _has_installed_linux_desktop_file():
        app.setDesktopFileName(APP_ID)

    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass


def _purge_local_pycache() -> None:
    root = Path(__file__).resolve().parents[2]
    for path in root.rglob('__pycache__'):
        try:
            shutil.rmtree(path)
        except Exception:
            pass


def main() -> int:
    _purge_local_pycache()
    from chisp_flasher.ui.main_window import MainWindow
    from chisp_flasher.ui.theme import APP_QSS

    app = QApplication(sys.argv)
    _apply_platform_metadata(app)
    app_icon = QIcon(_app_icon_path())
    app.setWindowIcon(app_icon)
    app.setStyleSheet(APP_QSS)
    window = MainWindow()
    window.setWindowIcon(app_icon)
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
