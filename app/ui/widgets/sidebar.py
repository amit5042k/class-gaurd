from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpacerItem, QSizePolicy, QFrame
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QFont

NAV_ITEMS = [
    ("MONITORING", [
        ("dashboard",         "▣  Dashboard"),
        ("live_view",         "▶  Live View"),
    ]),
    ("DEVICES", [
        ("cameras",           "●  Camera Management"),
        ("nvr",               "▦  NVR / DVR"),
    ]),
    ("AI FEATURES", [
        ("face_enrollment",   "☺  Face Enrollment"),
        ("attendance",        "□  Attendance"),
        ("expression",        "☻  Expression Monitor"),
        ("safety",            "⚠  Safety Watch"),
    ]),
    ("SYSTEM", [
        ("recordings",        "●  Recordings"),
        ("alerts",            "ὑ4  Alerts"),
        ("settings",          "⚙  Settings"),
    ]),
]


class Sidebar(QWidget):
    page_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self._buttons: dict[str, QPushButton] = {}
        self._current = ""
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Logo
        logo_area = QWidget()
        logo_area.setObjectName("logo_area")
        la = QVBoxLayout(logo_area)
        la.setContentsMargins(16, 18, 16, 8)
        title = QLabel("ClassGuard")
        title.setObjectName("app_title")
        sub = QLabel("Camera Management System")
        sub.setObjectName("app_sub")
        la.addWidget(title)
        la.addWidget(sub)
        layout.addWidget(logo_area)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #252550;")
        layout.addWidget(sep)

        # Nav sections
        for section_title, items in NAV_ITEMS:
            lbl = QLabel(section_title)
            lbl.setObjectName("nav_section_label")
            layout.addWidget(lbl)
            for key, text in items:
                btn = QPushButton(text)
                btn.setObjectName("nav_btn")
                btn.setCheckable(True)
                btn.setFixedHeight(40)
                btn.clicked.connect(lambda checked, k=key: self._on_nav(k))
                self._buttons[key] = btn
                layout.addWidget(btn)

        layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )

        # Camera count badge area
        badge_row = QHBoxLayout()
        badge_row.setContentsMargins(16, 8, 16, 16)
        self._cam_label = QLabel("Active Cameras")
        self._cam_label.setStyleSheet("color:#666; font-size:11px;")
        self._cam_badge = QLabel("0")
        self._cam_badge.setObjectName("cam_count_badge")
        badge_row.addWidget(self._cam_label)
        badge_row.addStretch()
        badge_row.addWidget(self._cam_badge)
        layout.addLayout(badge_row)

    def _on_nav(self, key: str):
        for k, btn in self._buttons.items():
            btn.setChecked(k == key)
        self._current = key
        self.page_changed.emit(key)

    def set_active(self, key: str):
        self._on_nav(key)

    def update_camera_count(self, count: int):
        self._cam_badge.setText(str(count))
