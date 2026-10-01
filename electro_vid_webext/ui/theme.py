APP_STYLESHEET = r"""
QWidget {
    background: #111827;
    color: #e5e7eb;
    font-family: "Segoe UI";
    font-size: 10pt;
}

QMainWindow {
    background: #0b1220;
}

QLabel#appTitle {
    color: #f8fafc;
    font-size: 24pt;
    font-weight: 700;
}

QLabel#subtitleLabel,
QLabel#secondaryLabel {
    color: #94a3b8;
}

QLabel#statusLabel {
    background: #172033;
    border: 1px solid #273449;
    border-radius: 8px;
    padding: 7px 10px;
    color: #cbd5e1;
}

QLineEdit,
QComboBox {
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 8px 10px;
    selection-background-color: #2563eb;
}

QLineEdit:focus,
QComboBox:focus {
    border: 1px solid #3b82f6;
}

QPushButton {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 7px 12px;
    min-height: 18px;
}

QPushButton:hover {
    background: #293548;
    border-color: #475569;
}

QPushButton:pressed {
    background: #0f172a;
}

QPushButton:disabled {
    color: #64748b;
    background: #111827;
    border-color: #1f2937;
}

QPushButton[role="primary"] {
    background: #2563eb;
    border-color: #3b82f6;
    color: white;
    font-weight: 600;
}

QPushButton[role="primary"]:hover {
    background: #1d4ed8;
}

QPushButton[role="success"] {
    background: #047857;
    border-color: #059669;
    color: white;
    font-weight: 600;
}

QPushButton[role="success"]:hover {
    background: #065f46;
}

QTabWidget::pane {
    border: 1px solid #273449;
    border-radius: 10px;
    background: #111827;
    top: -1px;
}

QTabBar::tab {
    background: #172033;
    color: #94a3b8;
    border: 1px solid #273449;
    padding: 9px 18px;
    margin-right: 3px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}

QTabBar::tab:selected {
    background: #111827;
    color: #f8fafc;
    border-bottom-color: #111827;
}

QTableWidget {
    background: #0f172a;
    alternate-background-color: #121c2f;
    border: 1px solid #273449;
    border-radius: 8px;
    gridline-color: #1f2a3b;
    selection-background-color: #1d4ed8;
    selection-color: white;
}

QHeaderView::section {
    background: #172033;
    color: #cbd5e1;
    border: none;
    border-right: 1px solid #273449;
    border-bottom: 1px solid #273449;
    padding: 8px;
    font-weight: 600;
}

QScrollBar:horizontal,
QScrollBar:vertical {
    background: #0f172a;
    border: none;
    margin: 0;
}

QScrollBar::handle:horizontal,
QScrollBar::handle:vertical {
    background: #475569;
    border-radius: 5px;
    min-width: 28px;
    min-height: 28px;
}

QScrollBar::handle:horizontal:hover,
QScrollBar::handle:vertical:hover {
    background: #64748b;
}

QProgressBar {
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 7px;
    text-align: center;
}

QProgressBar::chunk {
    background: #2563eb;
    border-radius: 6px;
}

QSlider::groove:horizontal {
    height: 5px;
    background: #334155;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: #60a5fa;
    width: 16px;
    margin: -6px 0;
    border-radius: 8px;
}

QToolTip {
    background: #0f172a;
    color: #f8fafc;
    border: 1px solid #475569;
    padding: 5px;
}
"""
