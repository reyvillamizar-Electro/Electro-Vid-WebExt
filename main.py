import sys

from PySide6.QtWidgets import QApplication

from electro_vid_webext.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Electro Vid-WebExt")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
