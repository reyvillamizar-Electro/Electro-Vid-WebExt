from __future__ import annotations

import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from electro_vid_webext.core.detector import VideoSource, detect_video_sources
from electro_vid_webext.core.metadata import MediaMetadata, ffprobe_available, read_media_metadata
from electro_vid_webext.core.mpv_player import MPVController, MPVError


class AnalysisWorker(QObject):
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(detect_video_sources(self.url))
        except Exception as exc:
            self.failed.emit(str(exc))


class MetadataWorker(QObject):
    item_ready = Signal(str, object)
    progress = Signal(int, int)
    finished = Signal()

    def __init__(self, sources: list[VideoSource]) -> None:
        super().__init__()
        self.sources = sources

    @Slot()
    def run(self) -> None:
        total = len(self.sources)
        if total == 0:
            self.finished.emit()
            return

        max_workers = min(3, total)
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="metadata") as executor:
            future_by_url = {
                executor.submit(read_media_metadata, source.url): source.url
                for source in self.sources
            }

            completed = 0
            for future in as_completed(future_by_url):
                url = future_by_url[future]
                try:
                    metadata = future.result()
                except Exception:
                    metadata = MediaMetadata()

                completed += 1
                self.item_ready.emit(url, metadata)
                self.progress.emit(completed, total)

        self.finished.emit()


class MainWindow(QMainWindow):
    URL_COLUMN = 6

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Electro Vid-WebExt")
        self.resize(1280, 760)

        self._analysis_thread: QThread | None = None
        self._analysis_worker: AnalysisWorker | None = None
        self._metadata_thread: QThread | None = None
        self._metadata_worker: MetadataWorker | None = None
        self._sources: list[VideoSource] = []
        self._row_by_url: dict[str, int] = {}

        self._mpv: MPVController | None = None
        self._preview_loaded = False
        self._duration_seconds = 0.0

        self._build_ui()

        self._player_timer = QTimer(self)
        self._player_timer.setInterval(500)
        self._player_timer.timeout.connect(self._refresh_player_state)

    def _build_ui(self) -> None:
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("Electro Vid-WebExt")
        title.setStyleSheet("font-size: 26px; font-weight: 700;")
        subtitle = QLabel("Detecta, inspecciona y previsualiza fuentes de video de una página web.")
        subtitle.setStyleSheet("color: #666;")

        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://sitio.com/pagina-con-video")
        self.url_input.returnPressed.connect(self.start_analysis)
        self.analyze_button = QPushButton("Analizar")
        self.analyze_button.clicked.connect(self.start_analysis)
        url_row.addWidget(self.url_input, 1)
        url_row.addWidget(self.analyze_button)

        self.status_label = QLabel("Listo. Pega una URL para comenzar.")

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_results_tab(), "Resultados")
        self.tabs.addTab(self._build_preview_tab(), "Previsualización")

        note = QLabel(
            "FFprobe obtiene duración, resolución y codec. mpv reproduce con hwdec=auto-safe: "
            "usa aceleración por hardware cuando es compatible y cae a software cuando no lo es."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #777;")

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(url_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(note)
        self.setCentralWidget(root)

    def _build_results_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Tipo", "Duración", "Calidad", "Codec", "Tamaño", "Detectado en", "URL"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self.preview_selected)

        actions = QHBoxLayout()
        self.preview_button = QPushButton("Previsualizar")
        self.open_button = QPushButton("Abrir fuente")
        self.copy_button = QPushButton("Copiar URL")
        self.preview_button.clicked.connect(self.preview_selected)
        self.open_button.clicked.connect(self.open_selected)
        self.copy_button.clicked.connect(self.copy_selected)
        actions.addWidget(self.preview_button)
        actions.addWidget(self.open_button)
        actions.addWidget(self.copy_button)
        actions.addStretch(1)

        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        return page

    def _build_preview_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.preview_title = QLabel("Selecciona un video y pulsa Previsualizar.")
        self.preview_title.setWordWrap(True)

        self.video_surface = QWidget()
        self.video_surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_surface.setMinimumHeight(420)
        self.video_surface.setStyleSheet("background: black;")

        controls = QHBoxLayout()
        self.play_button = QPushButton("▶ Reproducir")
        self.stop_button = QPushButton("■ Detener")
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.time_label = QLabel("00:00 / 00:00")

        self.play_button.clicked.connect(self.toggle_playback)
        self.stop_button.clicked.connect(self.stop_playback)
        self.position_slider.sliderReleased.connect(self._seek_from_slider)

        controls.addWidget(self.play_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.position_slider, 1)
        controls.addWidget(self.time_label)

        self.mpv_status = QLabel(
            "Motor: mpv" if MPVController.available() else "Motor: mpv no encontrado en PATH"
        )
        self.mpv_status.setStyleSheet("color: #777;")

        layout.addWidget(self.preview_title)
        layout.addWidget(self.video_surface, 1)
        layout.addLayout(controls)
        layout.addWidget(self.mpv_status)
        return page

    def _ensure_mpv(self) -> MPVController:
        if self._mpv is not None:
            return self._mpv

        controller = MPVController(int(self.video_surface.winId()))
        controller.start()
        self._mpv = controller
        self.mpv_status.setText("Motor: mpv · hardware seguro + fallback por software")
        return controller

    @Slot()
    def start_analysis(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.information(self, "URL requerida", "Pega una URL antes de analizar.")
            return

        if self._analysis_thread and self._analysis_thread.isRunning():
            return

        self.stop_playback()
        self.table.setRowCount(0)
        self._sources = []
        self._row_by_url = {}
        self.analyze_button.setEnabled(False)
        self.status_label.setText("Analizando página…")

        self._analysis_thread = QThread(self)
        self._analysis_worker = AnalysisWorker(url)
        self._analysis_worker.moveToThread(self._analysis_thread)
        self._analysis_thread.started.connect(self._analysis_worker.run)
        self._analysis_worker.finished.connect(self._analysis_finished)
        self._analysis_worker.failed.connect(self._analysis_failed)
        self._analysis_worker.finished.connect(self._analysis_thread.quit)
        self._analysis_worker.failed.connect(self._analysis_thread.quit)
        self._analysis_thread.finished.connect(self._analysis_worker.deleteLater)
        self._analysis_thread.finished.connect(self._analysis_thread.deleteLater)
        self._analysis_thread.finished.connect(self._cleanup_analysis_thread)
        self._analysis_thread.start()

    @Slot(list)
    def _analysis_finished(self, sources: list[VideoSource]) -> None:
        self._sources = sources

        for row, source in enumerate(sources):
            self.table.insertRow(row)
            self._row_by_url[source.url] = row
            values = [source.kind, "…", "…", "…", "…", source.origin, source.url]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))

        if sources:
            extra = "" if ffprobe_available() else " (ffprobe no detectado)"
            self.status_label.setText(
                f"{len(sources)} fuente(s) encontrada(s). Leyendo metadatos…{extra}"
            )
            self.table.selectRow(0)
            self._start_metadata_scan(sources)
        else:
            self.status_label.setText("No se encontraron fuentes directas en el HTML de esta página.")
            self.analyze_button.setEnabled(True)

    def _start_metadata_scan(self, sources: list[VideoSource]) -> None:
        if self._metadata_thread and self._metadata_thread.isRunning():
            return

        self._metadata_thread = QThread(self)
        self._metadata_worker = MetadataWorker(sources)
        self._metadata_worker.moveToThread(self._metadata_thread)
        self._metadata_thread.started.connect(self._metadata_worker.run)
        self._metadata_worker.item_ready.connect(self._metadata_ready)
        self._metadata_worker.progress.connect(self._metadata_progress)
        self._metadata_worker.finished.connect(self._metadata_thread.quit)
        self._metadata_thread.finished.connect(self._metadata_worker.deleteLater)
        self._metadata_thread.finished.connect(self._metadata_thread.deleteLater)
        self._metadata_thread.finished.connect(self._metadata_finished)
        self._metadata_thread.start()

    @Slot(str, object)
    def _metadata_ready(self, url: str, metadata: MediaMetadata) -> None:
        row = self._row_by_url.get(url)
        if row is None:
            return
        self.table.setItem(row, 1, QTableWidgetItem(metadata.duration))
        self.table.setItem(row, 2, QTableWidgetItem(metadata.quality))
        self.table.setItem(row, 3, QTableWidgetItem(metadata.codec))
        self.table.setItem(row, 4, QTableWidgetItem(metadata.size))

    @Slot(int, int)
    def _metadata_progress(self, completed: int, total: int) -> None:
        self.status_label.setText(
            f"{len(self._sources)} fuente(s) encontrada(s). Metadatos {completed}/{total}…"
        )

    @Slot()
    def _metadata_finished(self) -> None:
        self._metadata_thread = None
        self._metadata_worker = None
        self.analyze_button.setEnabled(True)
        self.status_label.setText(f"{len(self._sources)} fuente(s) de video lista(s).")

    @Slot(str)
    def _analysis_failed(self, message: str) -> None:
        self.status_label.setText("No se pudo analizar la página.")
        self.analyze_button.setEnabled(True)
        QMessageBox.warning(self, "Error de análisis", message)

    @Slot()
    def _cleanup_analysis_thread(self) -> None:
        self._analysis_thread = None
        self._analysis_worker = None

    def _selected_url(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, self.URL_COLUMN)
        return item.text() if item else None

    @Slot()
    def preview_selected(self) -> None:
        url = self._selected_url()
        if not url:
            QMessageBox.information(self, "Selecciona un video", "Selecciona una fila primero.")
            return

        try:
            player = self._ensure_mpv()
            player.load(url)
        except MPVError as exc:
            QMessageBox.warning(self, "mpv no disponible", str(exc))
            self.status_label.setText(str(exc))
            return

        self._preview_loaded = True
        self.preview_title.setText(url)
        self.tabs.setCurrentIndex(1)
        self.play_button.setText("⏸ Pausar")
        self._player_timer.start()
        self.status_label.setText("Reproduciendo con mpv.")

    @Slot()
    def toggle_playback(self) -> None:
        if not self._preview_loaded:
            return
        try:
            player = self._ensure_mpv()
            player.toggle_pause()
            paused = bool(player.get_property("pause"))
            self.play_button.setText("▶ Reproducir" if paused else "⏸ Pausar")
        except MPVError as exc:
            self.status_label.setText(str(exc))

    @Slot()
    def stop_playback(self) -> None:
        if self._mpv is not None:
            try:
                self._mpv.stop()
            except MPVError:
                pass
        self._preview_loaded = False
        self._player_timer.stop()
        self.position_slider.setRange(0, 0)
        self.time_label.setText("00:00 / 00:00")
        self.play_button.setText("▶ Reproducir")

    @Slot()
    def _seek_from_slider(self) -> None:
        if not self._preview_loaded:
            return
        try:
            self._ensure_mpv().seek_absolute(float(self.position_slider.value()))
        except MPVError as exc:
            self.status_label.setText(str(exc))

    @Slot()
    def _refresh_player_state(self) -> None:
        if not self._preview_loaded or self._mpv is None:
            return

        try:
            duration = self._mpv.get_property("duration")
            position = self._mpv.get_property("time-pos")
            paused = self._mpv.get_property("pause")
        except MPVError:
            return

        if isinstance(duration, (int, float)) and duration > 0:
            self._duration_seconds = float(duration)
            self.position_slider.setRange(0, int(duration))

        if isinstance(position, (int, float)) and not self.position_slider.isSliderDown():
            self.position_slider.setValue(int(position))

        self.play_button.setText("▶ Reproducir" if paused else "⏸ Pausar")
        self.time_label.setText(
            f"{self._format_seconds(position)} / {self._format_seconds(duration)}"
        )

    @Slot()
    def open_selected(self) -> None:
        url = self._selected_url()
        if url:
            webbrowser.open(url)

    @Slot()
    def copy_selected(self) -> None:
        url = self._selected_url()
        if url:
            QGuiApplication.clipboard().setText(url)
            self.status_label.setText("URL copiada al portapapeles.")

    @staticmethod
    def _format_seconds(value: object) -> str:
        if not isinstance(value, (int, float)) or value < 0:
            return "00:00"
        total = int(value)
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def closeEvent(self, event: QCloseEvent) -> None:
        self._player_timer.stop()
        if self._mpv is not None:
            self._mpv.close()
        super().closeEvent(event)
