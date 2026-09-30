from __future__ import annotations

import webbrowser

from PySide6.QtCore import QObject, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
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
from PySide6.QtCore import Qt

from electro_vid_webext.core.detector import VideoSource, detect_video_sources
from electro_vid_webext.core.metadata import MediaMetadata, ffprobe_available, read_media_metadata


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
    finished = Signal()

    def __init__(self, sources: list[VideoSource]) -> None:
        super().__init__()
        self.sources = sources

    @Slot()
    def run(self) -> None:
        for source in self.sources:
            try:
                metadata = read_media_metadata(source.url)
            except Exception:
                metadata = MediaMetadata()
            self.item_ready.emit(source.url, metadata)
        self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Electro Vid-WebExt")
        self.resize(1180, 720)

        self._analysis_thread: QThread | None = None
        self._analysis_worker: AnalysisWorker | None = None
        self._metadata_thread: QThread | None = None
        self._metadata_worker: MetadataWorker | None = None
        self._sources: list[VideoSource] = []
        self._row_by_url: dict[str, int] = {}

        self.audio_output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.7)

        self._build_ui()
        self._connect_player()

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
            "Duración y resolución se obtienen con ffprobe cuando está disponible. "
            "El tamaño se intenta obtener desde los encabezados HTTP. "
            "Streams HLS/DASH pueden no informar un tamaño único."
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

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Tipo", "Duración", "Calidad", "Tamaño", "Detectado en", "URL"]
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

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(360)
        self.player.setVideoOutput(self.video_widget)

        self.preview_title = QLabel("Selecciona un video y pulsa Previsualizar.")
        self.preview_title.setWordWrap(True)

        controls = QHBoxLayout()
        self.play_button = QPushButton("▶ Reproducir")
        self.stop_button = QPushButton("■ Detener")
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_label = QLabel("00:00 / 00:00")

        self.play_button.clicked.connect(self.toggle_playback)
        self.stop_button.clicked.connect(self.player.stop)
        self.position_slider.sliderMoved.connect(self.player.setPosition)

        controls.addWidget(self.play_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.position_slider, 1)
        controls.addWidget(self.time_label)

        layout.addWidget(self.preview_title)
        layout.addWidget(self.video_widget, 1)
        layout.addLayout(controls)
        return page

    def _connect_player(self) -> None:
        self.player.positionChanged.connect(self._update_position)
        self.player.durationChanged.connect(self._update_duration)
        self.player.playbackStateChanged.connect(self._update_play_button)
        self.player.errorOccurred.connect(self._player_error)

    @Slot()
    def start_analysis(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.information(self, "URL requerida", "Pega una URL antes de analizar.")
            return

        if self._analysis_thread and self._analysis_thread.isRunning():
            return

        self.player.stop()
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
            values = [source.kind, "…", "…", "…", source.origin, source.url]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))

        if sources:
            extra = "" if ffprobe_available() else " (ffprobe no detectado: duración/calidad pueden quedar vacías)"
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
        self.table.setItem(row, 3, QTableWidgetItem(metadata.size))

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
        item = self.table.item(row, 5)
        return item.text() if item else None

    @Slot()
    def preview_selected(self) -> None:
        url = self._selected_url()
        if not url:
            QMessageBox.information(self, "Selecciona un video", "Selecciona una fila primero.")
            return

        self.preview_title.setText(url)
        self.player.setSource(QUrl(url))
        self.tabs.setCurrentIndex(1)
        self.player.play()

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

    @Slot()
    def toggle_playback(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    @Slot(int)
    def _update_position(self, position: int) -> None:
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)
        self._refresh_time_label(position, self.player.duration())

    @Slot(int)
    def _update_duration(self, duration: int) -> None:
        self.position_slider.setRange(0, max(duration, 0))
        self._refresh_time_label(self.player.position(), duration)

    @Slot(object)
    def _update_play_button(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText("⏸ Pausar")
        else:
            self.play_button.setText("▶ Reproducir")

    @Slot(object, str)
    def _player_error(self, _error, error_string: str) -> None:
        if error_string:
            self.status_label.setText(f"Reproductor: {error_string}")

    def _refresh_time_label(self, position_ms: int, duration_ms: int) -> None:
        self.time_label.setText(
            f"{self._format_ms(position_ms)} / {self._format_ms(duration_ms)}"
        )

    @staticmethod
    def _format_ms(milliseconds: int) -> str:
        seconds = max(milliseconds, 0) // 1000
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"
