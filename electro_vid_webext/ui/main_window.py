from __future__ import annotations

import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QObject, QStandardPaths, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSlider,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from electro_vid_webext.core.detector import VideoSource, detect_video_sources
from electro_vid_webext.core.downloader import download_media, suggested_extension
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


class DownloadWorker(QObject):
    progress = Signal(int)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, url: str, destination: str, kind: str, referer: str | None) -> None:
        super().__init__()
        self.url = url
        self.destination = destination
        self.kind = kind
        self.referer = referer

    @Slot()
    def run(self) -> None:
        try:
            download_media(
                self.url,
                self.destination,
                self.kind,
                progress=self.progress.emit,
                referer=self.referer,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(self.destination)


class MainWindow(QMainWindow):
    KIND_COLUMN = 0
    URL_COLUMN = 6

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Electro Vid-WebExt")
        self.resize(1180, 680)
        self.setMinimumSize(760, 480)

        self._analysis_thread: QThread | None = None
        self._analysis_worker: AnalysisWorker | None = None
        self._metadata_thread: QThread | None = None
        self._metadata_worker: MetadataWorker | None = None
        self._download_thread: QThread | None = None
        self._download_worker: DownloadWorker | None = None
        self._download_dialog: QProgressDialog | None = None

        self._sources: list[VideoSource] = []
        self._row_by_url: dict[str, int] = {}

        self._mpv: MPVController | None = None
        self._preview_loaded = False
        self._preview_url: str | None = None
        self._preview_kind: str | None = None
        self._duration_seconds = 0.0
        self._loop_a: float | None = None
        self._loop_b: float | None = None
        self._fullscreen_preview = False

        self._build_ui()

        self._player_timer = QTimer(self)
        self._player_timer.setInterval(500)
        self._player_timer.timeout.connect(self._refresh_player_state)

    def _build_ui(self) -> None:
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        self.header_widget = QWidget()
        header_layout = QVBoxLayout(self.header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        title = QLabel("Electro Vid-WebExt")
        title.setStyleSheet("font-size: 25px; font-weight: 700;")
        subtitle = QLabel("Detecta, inspecciona, previsualiza y descarga fuentes de video web.")
        subtitle.setStyleSheet("color: #666;")

        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://sitio.com/pagina-con-video")
        self.url_input.returnPressed.connect(self.start_analysis)
        self.analyze_button = QPushButton("Analizar")
        self.analyze_button.clicked.connect(self.start_analysis)
        url_row.addWidget(self.url_input, 1)
        url_row.addWidget(self.analyze_button)

        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        header_layout.addLayout(url_row)

        self.status_label = QLabel("Listo. Pega una URL para comenzar.")

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_results_tab(), "Resultados")
        self.tabs.addTab(self._build_preview_tab(), "Previsualización")

        self.note_label = QLabel(
            "FFprobe obtiene duración, resolución y codec. mpv usa aceleración segura y "
            "fallback por software. Descarga solo fuentes accesibles normalmente por el navegador."
        )
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet("color: #777;")

        layout.addWidget(self.header_widget)
        layout.addWidget(self.status_label)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.note_label)
        self.setCentralWidget(root)

    def _build_results_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)

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
        self.download_button = QPushButton("⬇ Descargar")
        self.open_button = QPushButton("Abrir fuente")
        self.copy_button = QPushButton("Copiar URL")
        self.preview_button.clicked.connect(self.preview_selected)
        self.download_button.clicked.connect(self.download_selected)
        self.open_button.clicked.connect(self.open_selected)
        self.copy_button.clicked.connect(self.copy_selected)
        actions.addWidget(self.preview_button)
        actions.addWidget(self.download_button)
        actions.addWidget(self.open_button)
        actions.addWidget(self.copy_button)
        actions.addStretch(1)

        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        return page

    def _build_preview_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 6, 4, 4)
        layout.setSpacing(6)

        self.preview_title = QLabel("Selecciona un video y pulsa Previsualizar.")
        self.preview_title.setWordWrap(True)

        self.video_surface = QWidget()
        self.video_surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_surface.setMinimumHeight(120)
        self.video_surface.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Ignored,
        )
        self.video_surface.setStyleSheet("background: black;")

        timeline = QHBoxLayout()
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderReleased.connect(self._seek_from_slider)
        self.time_label = QLabel("00:00 / 00:00")
        timeline.addWidget(self.position_slider, 1)
        timeline.addWidget(self.time_label)

        controls = QHBoxLayout()
        self.play_button = QPushButton("▶ Reproducir")
        self.stop_button = QPushButton("■ Detener")
        self.loop_button = QPushButton("🔁 Bucle")
        self.loop_button.setCheckable(True)
        self.loop_a_button = QPushButton("A")
        self.loop_b_button = QPushButton("B")
        self.clear_ab_button = QPushButton("A–B ✕")
        self.download_preview_button = QPushButton("⬇ Descargar")
        self.fullscreen_button = QPushButton("⛶ Pantalla completa")

        self.play_button.clicked.connect(self.toggle_playback)
        self.stop_button.clicked.connect(self.stop_playback)
        self.loop_button.toggled.connect(self.toggle_file_loop)
        self.loop_a_button.clicked.connect(self.mark_loop_a)
        self.loop_b_button.clicked.connect(self.mark_loop_b)
        self.clear_ab_button.clicked.connect(self.clear_ab_loop)
        self.download_preview_button.clicked.connect(self.download_preview)
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen_preview)

        controls.addWidget(self.play_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.loop_button)
        controls.addWidget(self.loop_a_button)
        controls.addWidget(self.loop_b_button)
        controls.addWidget(self.clear_ab_button)
        controls.addWidget(self.download_preview_button)
        controls.addWidget(self.fullscreen_button)
        controls.addStretch(1)

        audio_controls = QHBoxLayout()
        self.mute_button = QPushButton("🔊")
        self.mute_button.setCheckable(True)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setMaximumWidth(180)
        self.volume_label = QLabel("70%")
        self.ab_label = QLabel("A: —   B: —")

        self.mute_button.toggled.connect(self.toggle_mute)
        self.volume_slider.valueChanged.connect(self.set_volume)

        audio_controls.addWidget(self.mute_button)
        audio_controls.addWidget(self.volume_slider)
        audio_controls.addWidget(self.volume_label)
        audio_controls.addSpacing(12)
        audio_controls.addWidget(self.ab_label)
        audio_controls.addStretch(1)

        path = MPVController.executable_path()
        self.mpv_status = QLabel(
            f"Motor: mpv · {path}" if path else "Motor: mpv no encontrado"
        )
        self.mpv_status.setStyleSheet("color: #777;")

        layout.addWidget(self.preview_title)
        layout.addWidget(self.video_surface, 1)
        layout.addLayout(timeline)
        layout.addLayout(controls)
        layout.addLayout(audio_controls)
        layout.addWidget(self.mpv_status)
        return page

    def _ensure_mpv(self) -> MPVController:
        if self._mpv is not None:
            return self._mpv

        controller = MPVController(int(self.video_surface.winId()))
        controller.start()
        controller.set_volume(self.volume_slider.value())
        self._mpv = controller
        self.mpv_status.setText(
            f"Motor: mpv · {controller.executable} · hardware seguro + fallback por software"
        )
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

    def _selected_value(self, column: int) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, column)
        return item.text() if item else None

    def _selected_url(self) -> str | None:
        return self._selected_value(self.URL_COLUMN)

    @Slot()
    def preview_selected(self) -> None:
        url = self._selected_url()
        if not url:
            QMessageBox.information(self, "Selecciona un video", "Selecciona una fila primero.")
            return

        try:
            player = self._ensure_mpv()
            player.load(url)
            player.set_file_loop(self.loop_button.isChecked())
            player.set_ab_loop(None, None)
        except MPVError as exc:
            QMessageBox.warning(self, "mpv no disponible", str(exc))
            self.status_label.setText(str(exc))
            return

        self._preview_url = url
        self._preview_kind = self._selected_value(self.KIND_COLUMN)
        self._loop_a = None
        self._loop_b = None
        self._update_ab_label()
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

    @Slot(bool)
    def toggle_file_loop(self, enabled: bool) -> None:
        self.loop_button.setText("🔁 Bucle ON" if enabled else "🔁 Bucle")
        if self._mpv is not None:
            try:
                self._mpv.set_file_loop(enabled)
            except MPVError as exc:
                self.status_label.setText(str(exc))

    def _current_position(self) -> float | None:
        if not self._preview_loaded or self._mpv is None:
            return None
        value = self._mpv.get_property("time-pos")
        return float(value) if isinstance(value, (int, float)) else None

    @Slot()
    def mark_loop_a(self) -> None:
        position = self._current_position()
        if position is None:
            return
        self._loop_a = position
        if self._loop_b is not None and self._loop_b <= self._loop_a:
            self._loop_b = None
        self._apply_ab_loop()

    @Slot()
    def mark_loop_b(self) -> None:
        position = self._current_position()
        if position is None:
            return
        if self._loop_a is None:
            self.status_label.setText("Marca primero el punto A del bucle.")
            return
        if position <= self._loop_a:
            self.status_label.setText("El punto B debe estar después del punto A.")
            return
        self._loop_b = position
        self._apply_ab_loop()

    def _apply_ab_loop(self) -> None:
        self._update_ab_label()
        if self._mpv is None:
            return
        try:
            self._mpv.set_ab_loop(self._loop_a, self._loop_b)
        except MPVError as exc:
            self.status_label.setText(str(exc))

    @Slot()
    def clear_ab_loop(self) -> None:
        self._loop_a = None
        self._loop_b = None
        self._apply_ab_loop()
        self.status_label.setText("Bucle A–B eliminado.")

    def _update_ab_label(self) -> None:
        self.ab_label.setText(
            f"A: {self._format_seconds(self._loop_a) if self._loop_a is not None else '—'}   "
            f"B: {self._format_seconds(self._loop_b) if self._loop_b is not None else '—'}"
        )

    @Slot(int)
    def set_volume(self, value: int) -> None:
        self.volume_label.setText(f"{value}%")
        if self._mpv is not None:
            try:
                self._mpv.set_volume(value)
            except MPVError:
                pass

    @Slot(bool)
    def toggle_mute(self, muted: bool) -> None:
        self.mute_button.setText("🔇" if muted else "🔊")
        if self._mpv is not None:
            try:
                self._mpv.set_mute(muted)
            except MPVError:
                pass

    @Slot()
    def toggle_fullscreen_preview(self) -> None:
        if not self._fullscreen_preview:
            self._fullscreen_preview = True
            self.tabs.setCurrentIndex(1)
            self.header_widget.hide()
            self.status_label.hide()
            self.note_label.hide()
            self.tabs.tabBar().hide()
            self.preview_title.hide()
            self.mpv_status.hide()
            self.fullscreen_button.setText("⛶ Salir pantalla completa")
            self.showFullScreen()
        else:
            self._exit_fullscreen_preview()

    def _exit_fullscreen_preview(self) -> None:
        if not self._fullscreen_preview:
            return
        self._fullscreen_preview = False
        self.header_widget.show()
        self.status_label.show()
        self.note_label.show()
        self.tabs.tabBar().show()
        self.preview_title.show()
        self.mpv_status.show()
        self.fullscreen_button.setText("⛶ Pantalla completa")
        self.showMaximized()

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

        duration = self._mpv.get_property("duration")
        position = self._mpv.get_property("time-pos")
        paused = self._mpv.get_property("pause")

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
    def download_selected(self) -> None:
        url = self._selected_url()
        kind = self._selected_value(self.KIND_COLUMN)
        if not url or not kind:
            QMessageBox.information(self, "Selecciona un video", "Selecciona una fila primero.")
            return
        self._start_download(url, kind)

    @Slot()
    def download_preview(self) -> None:
        if not self._preview_url or not self._preview_kind:
            QMessageBox.information(
                self,
                "Sin video en previsualización",
                "Previsualiza un video antes de descargarlo desde el reproductor.",
            )
            return
        self._start_download(self._preview_url, self._preview_kind)

    def _start_download(self, url: str, kind: str) -> None:
        if self._download_thread and self._download_thread.isRunning():
            QMessageBox.information(self, "Descarga en curso", "Espera a que termine la descarga actual.")
            return

        parsed_name = Path(unquote(urlparse(url).path)).name
        stem = Path(parsed_name).stem if parsed_name else "video"
        extension = suggested_extension(url, kind)
        default_name = f"{stem or 'video'}{extension}"
        downloads = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        initial = str(Path(downloads or str(Path.home())) / default_name)

        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Guardar video",
            initial,
            "Archivos multimedia (*.*)",
        )
        if not destination:
            return

        self._download_dialog = QProgressDialog("Descargando video…", "", 0, 100, self)
        self._download_dialog.setWindowTitle("Electro Vid-WebExt")
        self._download_dialog.setCancelButton(None)
        self._download_dialog.setMinimumDuration(0)
        self._download_dialog.setValue(0)

        referer = self.url_input.text().strip() or None
        self._download_thread = QThread(self)
        self._download_worker = DownloadWorker(url, destination, kind, referer)
        self._download_worker.moveToThread(self._download_thread)
        self._download_thread.started.connect(self._download_worker.run)
        self._download_worker.progress.connect(self._download_progress)
        self._download_worker.finished.connect(self._download_finished)
        self._download_worker.failed.connect(self._download_failed)
        self._download_worker.finished.connect(self._download_thread.quit)
        self._download_worker.failed.connect(self._download_thread.quit)
        self._download_thread.finished.connect(self._download_worker.deleteLater)
        self._download_thread.finished.connect(self._download_thread.deleteLater)
        self._download_thread.finished.connect(self._cleanup_download)
        self._download_thread.start()

    @Slot(int)
    def _download_progress(self, value: int) -> None:
        if self._download_dialog is None:
            return
        if value < 0:
            self._download_dialog.setRange(0, 0)
            self._download_dialog.setLabelText("Procesando stream con FFmpeg…")
        else:
            if self._download_dialog.maximum() == 0:
                self._download_dialog.setRange(0, 100)
            self._download_dialog.setValue(value)

    @Slot(str)
    def _download_finished(self, destination: str) -> None:
        if self._download_dialog is not None:
            self._download_dialog.setRange(0, 100)
            self._download_dialog.setValue(100)
            self._download_dialog.close()
        self.status_label.setText(f"Descarga completada: {destination}")
        QMessageBox.information(self, "Descarga completada", f"Guardado en:\n{destination}")

    @Slot(str)
    def _download_failed(self, message: str) -> None:
        if self._download_dialog is not None:
            self._download_dialog.close()
        self.status_label.setText("La descarga falló.")
        QMessageBox.warning(self, "Error de descarga", message)

    @Slot()
    def _cleanup_download(self) -> None:
        self._download_thread = None
        self._download_worker = None
        self._download_dialog = None

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

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self._fullscreen_preview:
            self._exit_fullscreen_preview()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._player_timer.stop()
        if self._mpv is not None:
            self._mpv.close()
        super().closeEvent(event)
