import sys

from PySide6.QtWidgets import QApplication

from electro_vid_webext.ui.main_window import MainWindow
from electro_vid_webext.ui.theme import APP_STYLESHEET


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Electro Vid-WebExt")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)

    window = MainWindow()
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
