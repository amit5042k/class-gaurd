from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpacerItem, QSizePolicy, QFrame
)
from PyQt6.QtCore import pyqtSignal

NAV_ITEMS = [
    ("MONITORING", [
        ("dashboard",       "▣  Dashboard"),
        ("live_view",       "▶  Live View"),
    ]),
    ("DEVICES", [
        ("cameras",         "●  Camera Management"),
        ("nvr",             "▦  NVR / DVR"),
    ]),
    ("AI FEATURES", [
        ("face_enrollment", "☺  Face Enrollment"),
        ("attendance",      "□  Attendance"),
        ("expression",      "☻  Expression Monitor"),
        ("safety",          "⚠  Safety Watch"),
    ]),
    ("SYSTEM", [
        ("recordings",      "●  Recordings"),
        ("alerts",          "\U0001F514  Alerts"),
        ("settings",        "⚙  Settings"),
        ("users",           "\U0001F464  Users && Audit"),
    ]),
]


class Sidebar(QWidget):
    page_changed = pyqtSignal(str)
    lock_requested = pyqtSignal()
    password_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self._buttons: dict[str, QPushButton] = {}
        self._sections = []  # (label, [keys])
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        logo_area = QWidget()
        logo_area.setObjectName("logo_area")
        la = QVBoxLayout(logo_area)
        la.setContentsMargins(16, 18, 16, 8)
        title = QLabel("ClassGuard")
        title.setObjectName("app_title")
        from app.config import Config
        sub = QLabel(f"Camera Management System · v{Config.VERSION}")
        sub.setObjectName("app_sub")
        la.addWidget(title)
        la.addWidget(sub)
        layout.addWidget(logo_area)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #252550;")
        layout.addWidget(sep)

        for section_title, items in NAV_ITEMS:
            lbl = QLabel(section_title)
            lbl.setObjectName("nav_section_label")
            layout.addWidget(lbl)
            self._sections.append((lbl, [k for k, _ in items]))
            for key, text in items:
                btn = QPushButton(text)
                btn.setObjectName("nav_btn")
                btn.setCheckable(True)
                btn.setFixedHeight(34)
                btn.clicked.connect(lambda checked, k=key: self._on_nav(k))
                self._buttons[key] = btn
                layout.addWidget(btn)

        layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Minimum,
                        QSizePolicy.Policy.Expanding)
        )

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

        user_box = QWidget()
        ub = QVBoxLayout(user_box)
        ub.setContentsMargins(16, 0, 16, 14)
        self._user_lbl = QLabel()
        self._user_lbl.setStyleSheet("color:#9098b1; font-size:11px;")
        ub.addWidget(self._user_lbl)
        row = QHBoxLayout()
        lock = QPushButton("Lock")
        lock.setObjectName("secondary_btn")
        lock.setToolTip("Lock the screen or switch user (cameras keep recording)")
        lock.clicked.connect(self.lock_requested)
        pw = QPushButton("Password")
        pw.setObjectName("secondary_btn")
        pw.clicked.connect(self.password_requested)
        row.addWidget(lock)
        row.addWidget(pw)
        ub.addLayout(row)
        layout.addWidget(user_box)

    def _on_nav(self, key: str):
        for k, btn in self._buttons.items():
            btn.setChecked(k == key)
        self.page_changed.emit(key)

    def set_active(self, key: str):
        self._on_nav(key)

    def set_user(self, name: str, role_label: str):
        self._user_lbl.setText(f"Signed in: {name}\n{role_label}")

    def set_allowed(self, allowed: set):
        for key, btn in self._buttons.items():
            btn.setVisible(key in allowed)
        for lbl, keys in self._sections:
            lbl.setVisible(any(k in allowed for k in keys))

    def is_allowed(self, key: str) -> bool:
        btn = self._buttons.get(key)
        return bool(btn and not btn.isHidden())

    def update_camera_count(self, count: int):
        self._cam_badge.setText(str(count))
