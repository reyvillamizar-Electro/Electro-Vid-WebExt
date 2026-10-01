import sys

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineWidgets import QWebEngineView


class TestBrowser(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("QtWebEngine Test Browser")
        self.resize(1280, 820)

        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        toolbar = QHBoxLayout()

        self.back_button = QPushButton("←")
        self.forward_button = QPushButton("→")
        self.reload_button = QPushButton("↻")
        self.home_button = QPushButton("⌂")

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Pega una URL y presiona Enter")

        self.browser = QWebEngineView()

        toolbar.addWidget(self.back_button)
        toolbar.addWidget(self.forward_button)
        toolbar.addWidget(self.reload_button)
        toolbar.addWidget(self.home_button)
        toolbar.addWidget(self.url_input, 1)

        layout.addLayout(toolbar)
        layout.addWidget(self.browser, 1)

        self.back_button.clicked.connect(self.browser.back)
        self.forward_button.clicked.connect(self.browser.forward)
        self.reload_button.clicked.connect(self.browser.reload)
        self.home_button.clicked.connect(self.go_home)

        self.url_input.returnPressed.connect(self.navigate)
        self.browser.urlChanged.connect(self.sync_url)
        self.browser.titleChanged.connect(self.sync_title)

        self.go_home()

    def go_home(self) -> None:
        self.browser.setUrl(QUrl("https://www.google.com"))

    def navigate(self) -> None:
        value = self.url_input.text().strip()
        if not value:
            return
        if "://" not in value:
            value = "https://" + value
        self.browser.setUrl(QUrl(value))

    def sync_url(self, url: QUrl) -> None:
        self.url_input.setText(url.toString())

    def sync_title(self, title: str) -> None:
        self.setWindowTitle(f"{title} — QtWebEngine Test Browser")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("QtWebEngine Test Browser")

    window = TestBrowser()
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
