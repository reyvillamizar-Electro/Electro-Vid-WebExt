import os
import re
import sys


def _harden_chromium() -> None:
    """Disable browser authentication features not needed by the extractor."""
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
    features = {"WebAuthentication", "FedCm"}

    match = re.search(r"--disable-features=([^\s]+)", flags)
    if match:
        current = {item for item in match.group(1).split(",") if item}
        merged = ",".join(sorted(current | features))
        flags = (
            flags[: match.start()]
            + f"--disable-features={merged}"
            + flags[match.end() :]
        )
    else:
        disable = "--disable-features=" + ",".join(sorted(features))
        flags = f"{flags} {disable}".strip()

    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags


# Must run before importing/initializing QtWebEngine.
_harden_chromium()

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
