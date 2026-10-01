from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QProcess, QProcessEnvironment, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from electro_vid_webext.core.detector import VideoSource


class BrowserView(QWidget):
    media_found = Signal(object)
    navigation_event = Signal(object)
    embedded_page_found = Signal(str)
    page_ready = Signal()
    player_control_event = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._process: QProcess | None = None
        self._stdout_buffer = ""
        self._initial_url = ""
        self._current_url = ""
        self._debug_port = 0
        self._user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0"
        )
        self._shutting_down = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(8)

        title = QLabel("Navegador: Microsoft Edge WebView2")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        self._status = QLabel(
            "Al analizar una URL se abrirá una ventana WebView2 separada. "
            "Esa ventana reproduce H.264/AAC de forma nativa y las fuentes "
            "detectadas regresan automáticamente a Resultados."
        )
        self._status.setWordWrap(True)
        self._current_label = QLabel("Sin página cargada.")
        self._current_label.setWordWrap(True)

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(self._status)
        layout.addWidget(self._current_label)
        layout.addStretch(1)

    def _bridge_path(self) -> Path:
        return Path(__file__).resolve().parents[2] / "webview2_bridge.py"

    def _stop_process(self) -> None:
        process = self._process
        if process is None:
            return

        if process.state() != QProcess.ProcessState.NotRunning:
            try:
                self._send({"action": "close"})
                process.waitForFinished(800)
            except Exception:
                pass

        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(1200)

        process.deleteLater()
        self._process = None

    def load_page(self, url: str) -> None:
        if self._shutting_down:
            return

        self._stop_process()
        self._stdout_buffer = ""
        self._initial_url = url
        self._current_url = url
        self._current_label.setText(f"WebView2: {url}")
        self._status.setText(
            "Abriendo WebView2… interactúa con la ventana del navegador. "
            "Los reproductores embebidos y HLS se detectarán automáticamente."
        )

        self.navigation_event.emit(
            {
                "event": "URL inicial",
                "from": "",
                "to": url,
                "detail": "Microsoft Edge WebView2",
            }
        )

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.finished.connect(self._process_finished)

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            self._debug_port = int(probe.getsockname()[1])

        existing_args = env.value("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS")
        debug_args = (
            f"--remote-debugging-address=127.0.0.1 "
            f"--remote-debugging-port={self._debug_port} "
            f"--remote-allow-origins=http://127.0.0.1:{self._debug_port}"
        )
        env.insert(
            "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
            f"{existing_args} {debug_args}".strip(),
        )
        process.setProcessEnvironment(env)

        process.setProgram(sys.executable)
        process.setArguments(
            [
                str(self._bridge_path()),
                url,
                "--auto-player",
            ]
        )
        self._process = process
        process.start()

        if not process.waitForStarted(4000):
            self._status.setText(
                "No se pudo iniciar WebView2. Comprueba que Microsoft Edge "
                "WebView2 Runtime esté instalado."
            )

    def _read_stdout(self) -> None:
        process = self._process
        if process is None:
            return

        self._stdout_buffer += bytes(process.readAllStandardOutput()).decode(
            "utf-8",
            errors="replace",
        )

        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                self._handle_event(event)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None:
            return
        text = bytes(process.readAllStandardError()).decode(
            "utf-8",
            errors="replace",
        ).strip()
        if text:
            self.navigation_event.emit(
                {
                    "event": "WebView2 diagnóstico",
                    "from": self._current_url,
                    "to": "",
                    "detail": text[-1200:],
                }
            )

    def _handle_event(self, event: dict) -> None:
        event_type = str(event.get("type") or "")

        if event_type == "ready":
            self._status.setText(
                "WebView2 activo. Los popups y redirecciones publicitarias "
                "se interceptan; las fuentes multimedia vuelven a Resultados."
            )
            self.page_ready.emit()
            return

        if event_type == "status":
            message = str(event.get("message") or "")
            if message:
                self._status.setText(message)
            return

        if event_type == "diagnostic":
            self.navigation_event.emit(
                {
                    "event": "WebView2 diagnóstico",
                    "from": self._current_url,
                    "to": "",
                    "detail": str(event.get("message") or ""),
                }
            )
            return

        if event_type == "closed":
            self._status.setText("La ventana WebView2 se cerró.")
            return

        if event_type == "player_control":
            self.player_control_event.emit(event)
            return

        if event_type == "navigation":
            target = str(event.get("to") or "")
            if target:
                self._current_url = target
                self._current_label.setText(f"WebView2: {target}")
            self.navigation_event.emit(
                {
                    "event": str(event.get("event") or "Navegación"),
                    "from": str(event.get("from_url") or ""),
                    "to": target,
                    "detail": str(event.get("detail") or ""),
                }
            )
            return

        if event_type == "iframe":
            url = str(event.get("url") or "")
            if not url:
                return
            self.navigation_event.emit(
                {
                    "event": "Iframe / subframe",
                    "from": str(event.get("from_url") or self._current_url),
                    "to": url,
                    "detail": "Detectado por WebView2",
                }
            )
            self.embedded_page_found.emit(url)
            return

        if event_type == "media":
            url = str(event.get("url") or "")
            if not url:
                return
            user_agent = str(event.get("user_agent") or "").strip()
            if user_agent:
                self._user_agent = user_agent
            self.media_found.emit(
                VideoSource(
                    url=url,
                    kind=str(event.get("kind") or "Media"),
                    origin=str(event.get("origin") or "WebView2"),
                    referer=str(event.get("referer") or "") or self._current_url or None,
                    user_agent=self._user_agent,
                    cookie_header=str(event.get("cookie_header") or "") or None,
                    origin_header=str(event.get("origin_header") or "") or None,
                )
            )

    def _process_finished(self) -> None:
        if not self._shutting_down:
            self._status.setText(
                "La ventana WebView2 se cerró. Pulsa Analizar para abrirla nuevamente."
            )

    def _send(self, payload: dict) -> None:
        process = self._process
        if (
            process is None
            or process.state() != QProcess.ProcessState.Running
        ):
            return
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        process.write(data)

    def debug_port(self) -> int:
        return self._debug_port

    def prepare_recording_playback(self) -> None:
        self._send({"action": "prepare_recording"})

    def back(self) -> None:
        self._send({"action": "back"})

    def reload(self) -> None:
        self._send({"action": "reload"})

    def rescan_dom(self) -> None:
        self._send({"action": "rescan"})

    def set_media_compatibility(self, enabled: bool) -> None:
        # WebView2 has native H.264/AAC support; no compatibility shim is used.
        return

    def media_compatibility_enabled(self) -> bool:
        return False

    def cookie_header_for(self, url: str) -> str | None:
        return None

    def session_source(
        self,
        url: str,
        kind: str,
        origin: str,
        referer: str | None = None,
        origin_header: str | None = None,
        cookie_header: str | None = None,
    ) -> VideoSource:
        return VideoSource(
            url=url,
            kind=kind,
            origin=origin,
            referer=referer or self._current_url or None,
            user_agent=self._user_agent,
            cookie_header=cookie_header,
            origin_header=origin_header,
        )

    def shutdown(self) -> None:
        self._shutting_down = True
        self._stop_process()
