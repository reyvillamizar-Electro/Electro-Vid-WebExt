from __future__ import annotations

import webbrowser

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from electro_vid_webext.core.detector import VideoSource, detect_video_sources


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
        except Exception as exc:  # surfaced to the desktop UI
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Electro Vid-WebExt")
        self.resize(1000, 640)
        self._thread: QThread | None = None
        self._worker: AnalysisWorker | None = None
        self._sources: list[VideoSource] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("Electro Vid-WebExt")
        title.setStyleSheet("font-size: 26px; font-weight: 700;")
        subtitle = QLabel("Detecta fuentes de video expuestas directamente por una página web.")
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

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Tipo", "Detectado en", "URL"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self.open_selected)

        actions = QHBoxLayout()
        self.open_button = QPushButton("Abrir fuente")
        self.copy_button = QPushButton("Copiar URL")
        self.open_button.clicked.connect(self.open_selected)
        self.copy_button.clicked.connect(self.copy_selected)
        actions.addWidget(self.open_button)
        actions.addWidget(self.copy_button)
        actions.addStretch(1)

        note = QLabel(
            "Versión inicial: analiza el HTML recibido del servidor. "
            "Los reproductores creados dinámicamente con JavaScript se añadirán en la siguiente etapa."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #777;")

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(url_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        layout.addWidget(note)
        self.setCentralWidget(root)

    @Slot()
    def start_analysis(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.information(self, "URL requerida", "Pega una URL antes de analizar.")
            return

        if self._thread and self._thread.isRunning():
            return

        self.table.setRowCount(0)
        self._sources = []
        self.analyze_button.setEnabled(False)
        self.status_label.setText("Analizando página…")

        self._thread = QThread(self)
        self._worker = AnalysisWorker(url)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._analysis_finished)
        self._worker.failed.connect(self._analysis_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    @Slot(list)
    def _analysis_finished(self, sources: list[VideoSource]) -> None:
        self._sources = sources
        for row, source in enumerate(sources):
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(source.kind))
            self.table.setItem(row, 1, QTableWidgetItem(source.origin))
            self.table.setItem(row, 2, QTableWidgetItem(source.url))

        if sources:
            self.status_label.setText(f"{len(sources)} fuente(s) de video encontrada(s).")
            self.table.selectRow(0)
        else:
            self.status_label.setText("No se encontraron fuentes directas en el HTML de esta página.")
        self.analyze_button.setEnabled(True)

    @Slot(str)
    def _analysis_failed(self, message: str) -> None:
        self.status_label.setText("No se pudo analizar la página.")
        self.analyze_button.setEnabled(True)
        QMessageBox.warning(self, "Error de análisis", message)

    @Slot()
    def _cleanup_thread(self) -> None:
        self._thread = None
        self._worker = None

    def _selected_url(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 2)
        return item.text() if item else None

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
