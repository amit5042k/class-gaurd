DARK = """
QMainWindow, QDialog, QWidget {
    background-color: #12121f;
    color: #dde1ec;
    font-family: 'Segoe UI', 'Roboto', Arial, sans-serif;
    font-size: 13px;
}
QSplitter::handle { background-color: #1b1b30; }

/* ── Sidebar ─────────────────────────────────── */
#sidebar {
    background-color: #1b1b30;
    min-width: 230px; max-width: 230px;
    border-right: 1px solid #252550;
}
#logo_area { padding: 18px 16px 8px 16px; }
#app_title { color: #ef4565; font-size: 17px; font-weight: bold; }
#app_sub   { color: #666; font-size: 10px; }
#nav_section_label {
    color: #555; font-size: 10px; font-weight: bold;
    padding: 12px 20px 4px 20px; letter-spacing: 1px;
}
QPushButton#nav_btn {
    background: transparent; color: #9098b1;
    border: none; text-align: left;
    padding: 10px 20px; font-size: 13px; border-radius: 0;
}
QPushButton#nav_btn:hover  { background: #252550; color: #dde1ec; }
QPushButton#nav_btn:checked {
    background: #ef4565; color: #fff;
    font-weight: bold; border-left: 4px solid #ff8095;
}
#cam_count_badge {
    background: #ef4565; color: white;
    border-radius: 10px; font-size: 11px;
    min-width: 20px; padding: 1px 6px;
}

/* ── Page header ─────────────────────────────── */
#page_title    { font-size: 20px; font-weight: bold; color: #dde1ec; }
#page_subtitle { color: #666; font-size: 11px; }

/* ── Card ────────────────────────────────────── */
#card {
    background: #1b1b30; border-radius: 8px;
    border: 1px solid #252550; padding: 16px;
}
#card_title  { font-size: 13px; font-weight: bold; color: #9098b1; }
#stat_value  { font-size: 30px; font-weight: bold; color: #ef4565; }
#stat_label  { color: #666; font-size: 11px; }

/* ── Buttons ─────────────────────────────────── */
QPushButton { border-radius: 4px; padding: 6px 16px; font-size: 13px; }
QPushButton#primary_btn {
    background: #ef4565; color: white; border: none; font-weight: bold;
}
QPushButton#primary_btn:hover    { background: #d63656; }
QPushButton#primary_btn:disabled { background: #5a2030; color: #888; }
QPushButton#secondary_btn {
    background: #252550; color: #dde1ec;
    border: 1px solid #353580;
}
QPushButton#secondary_btn:hover { background: #303065; }
QPushButton#danger_btn  { background: #c0392b; color: white; border: none; }
QPushButton#success_btn { background: #27ae60; color: white; border: none; }
QPushButton#icon_btn {
    background: transparent; border: none;
    color: #9098b1; padding: 4px 8px;
}
QPushButton#icon_btn:hover { color: #ef4565; }

/* ── Table ───────────────────────────────────── */
QTableWidget {
    background: #1b1b30; gridline-color: #252550;
    color: #dde1ec; border: 1px solid #252550;
    border-radius: 4px; alternate-background-color: #1f1f38;
}
QTableWidget::item { padding: 6px 8px; }
QTableWidget::item:selected { background: #ef4565; color: white; }
QTableWidget::item:hover    { background: #252550; }
QHeaderView::section {
    background: #252550; color: #9098b1;
    padding: 8px; border: none; font-weight: bold; font-size: 12px;
}

/* ── Inputs ──────────────────────────────────── */
QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {
    background: #252550; color: #dde1ec;
    border: 1px solid #353580; border-radius: 4px;
    padding: 6px 10px; min-height: 28px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #ef4565;
}
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: #1b1b30; color: #dde1ec;
    selection-background-color: #ef4565;
    border: 1px solid #252550;
}
QSpinBox::up-button, QSpinBox::down-button { width: 16px; }

/* ── Scrollbar ───────────────────────────────── */
QScrollBar:vertical   { background: #1b1b30; width: 8px;  border-radius: 4px; }
QScrollBar:horizontal { background: #1b1b30; height: 8px; border-radius: 4px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #353580; border-radius: 4px; min-height: 20px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: #ef4565;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { height: 0; width: 0; }

/* ── Tab ─────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #252550; background: #1b1b30; border-radius: 4px;
}
QTabBar::tab {
    background: #252550; color: #666;
    padding: 8px 20px; margin-right: 2px;
    border-top-left-radius: 4px; border-top-right-radius: 4px;
}
QTabBar::tab:selected { background: #ef4565; color: white; }

/* ── GroupBox ────────────────────────────────── */
QGroupBox {
    color: #666; border: 1px solid #252550;
    border-radius: 6px; margin-top: 14px; padding-top: 10px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 10px;
    padding: 0 6px; color: #ef4565;
}

/* ── CheckBox ────────────────────────────────── */
QCheckBox { color: #dde1ec; spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border-radius: 3px; border: 1px solid #353580;
    background: #252550;
}
QCheckBox::indicator:checked { background: #ef4565; border-color: #ef4565; }

/* ── ProgressBar ─────────────────────────────── */
QProgressBar {
    background: #252550; border-radius: 4px;
    text-align: center; color: white;
}
QProgressBar::chunk { background: #ef4565; border-radius: 4px; }

/* ── Status bar ──────────────────────────────── */
QStatusBar {
    background: #0d0d1a; color: #666;
    border-top: 1px solid #252550; font-size: 11px;
}

/* ── Video cell ──────────────────────────────── */
#video_cell {
    background: #09090f; border: 1px solid #252550;
    border-radius: 4px;
}
#video_cell:hover { border: 1px solid #ef4565; }
#video_canvas { background: #000; }
#cam_name_bar {
    background-color: rgba(0,0,0,190);
    color: #dde1ec; font-size: 11px; padding: 2px 8px;
}
#connected_dot    { color: #27ae60; font-size: 14px; }
#disconnected_dot { color: #ef4565; font-size: 14px; }
#recording_dot    { color: #ef4565; }

/* ── Alert list ──────────────────────────────── */
#alert_item_high   { border-left: 4px solid #e74c3c; background: #1f1520; }
#alert_item_medium { border-left: 4px solid #f39c12; background: #1f1b14; }
#alert_item_low    { border-left: 4px solid #3498db; background: #14181f; }

/* ── Dialog ──────────────────────────────────── */
QDialog { background: #1b1b30; }
QLabel  { color: #dde1ec; }
QLabel#form_label { color: #9098b1; font-size: 12px; }
"""
