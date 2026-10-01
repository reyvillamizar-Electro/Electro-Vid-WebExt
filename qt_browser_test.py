import json
import sys

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
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
        self.codec_button = QPushButton("Diagnóstico codecs")

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Pega una URL y presiona Enter")

        self.browser = QWebEngineView()
        self.codec_output = QTextEdit()
        self.codec_output.setReadOnly(True)
        self.codec_output.setMaximumHeight(210)
        self.codec_output.setPlaceholderText(
            "Pulsa 'Diagnóstico codecs' para consultar canPlayType() dentro de QtWebEngine."
        )

        toolbar.addWidget(self.back_button)
        toolbar.addWidget(self.forward_button)
        toolbar.addWidget(self.reload_button)
        toolbar.addWidget(self.home_button)
        toolbar.addWidget(self.codec_button)
        toolbar.addWidget(self.url_input, 1)

        layout.addLayout(toolbar)
        layout.addWidget(self.browser, 1)
        layout.addWidget(self.codec_output)

        self.back_button.clicked.connect(self.browser.back)
        self.forward_button.clicked.connect(self.browser.forward)
        self.reload_button.clicked.connect(self.browser.reload)
        self.home_button.clicked.connect(self.go_home)
        self.codec_button.clicked.connect(self.run_codec_diagnostic)

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

    def run_codec_diagnostic(self) -> None:
        script = """
        (() => {
          const video = document.createElement('video');
          const audio = document.createElement('audio');

          const tests = [
            ['H.264 / AVC (MP4)', 'video', 'video/mp4; codecs="avc1.42E01E"'],
            ['H.264 High (MP4)', 'video', 'video/mp4; codecs="avc1.640028"'],
            ['HEVC / H.265 (MP4)', 'video', 'video/mp4; codecs="hvc1.1.6.L93.B0"'],
            ['VP9 (WebM)', 'video', 'video/webm; codecs="vp09.00.10.08"'],
            ['VP8 (WebM)', 'video', 'video/webm; codecs="vp8"'],
            ['AV1 (MP4)', 'video', 'video/mp4; codecs="av01.0.05M.08"'],
            ['AV1 (WebM)', 'video', 'video/webm; codecs="av01.0.05M.08"'],
            ['MP4 sin codec', 'video', 'video/mp4'],
            ['WebM sin codec', 'video', 'video/webm'],
            ['AAC-LC (MP4)', 'audio', 'audio/mp4; codecs="mp4a.40.2"'],
            ['AAC-HE (MP4)', 'audio', 'audio/mp4; codecs="mp4a.40.5"'],
            ['Opus (WebM)', 'audio', 'audio/webm; codecs="opus"'],
            ['Vorbis (WebM)', 'audio', 'audio/webm; codecs="vorbis"']
          ];

          return JSON.stringify({
            userAgent: navigator.userAgent,
            platform: navigator.platform,
            tests: tests.map(([name, kind, mime]) => {
              const element = kind === 'video' ? video : audio;
              return {
                name,
                mime,
                result: element.canPlayType(mime) || ''
              };
            })
          });
        })();
        """
        self.codec_output.setPlainText("Ejecutando diagnóstico…")
        self.browser.page().runJavaScript(script, self._show_codec_results)

    def _show_codec_results(self, values) -> None:
        if not isinstance(values, str) or not values:
            self.codec_output.setPlainText(
                "QtWebEngine no devolvió el diagnóstico. "
                "Resultado bruto: " + repr(values)
            )
            return

        try:
            values = json.loads(values)
        except json.JSONDecodeError:
            self.codec_output.setPlainText(
                "No se pudo interpretar el diagnóstico. "
                "Resultado bruto: " + values
            )
            return

        if not isinstance(values, dict):
            self.codec_output.setPlainText(
                "El diagnóstico devolvió un tipo inesperado: " + repr(values)
            )
            return

        tests = values.get("tests")
        if not isinstance(tests, list):
            self.codec_output.setPlainText(
                "El diagnóstico no incluyó la lista de codecs: " + repr(values)
            )
            return

        lines = [
            "Resultado de canPlayType() en QtWebEngine:",
            f"User-Agent: {values.get('userAgent', '—')}",
            f"Plataforma: {values.get('platform', '—')}",
            "",
            "probably = soporte fuerte",
            "maybe     = soporte posible",
            "NO        = el navegador no declara soporte",
            "",
        ]

        for item in tests:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            mime = str(item.get("mime") or "")
            raw = str(item.get("result") or "")
            shown = raw if raw else "NO"
            lines.append(f"{name}: {shown}")
            lines.append(f"  {mime}")

        self.codec_output.setPlainText("\n".join(lines))

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
