from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QGridLayout, QScrollArea, QFrame
)
from PyQt6.QtCore import Qt, QTimer
from datetime import datetime


class StatCard(QWidget):
    def __init__(self, label: str, value: str, color: str = "#ef4565", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        lbl = QLabel(label)
        lbl.setObjectName("card_title")
        self._val = QLabel(value)
        self._val.setObjectName("stat_value")
        self._val.setStyleSheet(f"color:{color}; font-size:30px; font-weight:bold;")
        layout.addWidget(lbl)
        layout.addWidget(self._val)

    def set_value(self, v: str):
        self._val.setText(v)


class Dashboard(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._build_ui()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(10_000)
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(16)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setObjectName("page_title")
        self._clock = QLabel()
        self._clock.setStyleSheet("color:#666; font-size:12px;")
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(self._clock)
        root.addLayout(hdr)
        QTimer(self, timeout=self._tick).start(1000)

        # Stat cards
        grid = QGridLayout()
        grid.setSpacing(12)
        self._s_cameras   = StatCard("Total Cameras",    "0", "#3498db")
        self._s_active    = StatCard("Active Streams",   "0", "#27ae60")
        self._s_persons   = StatCard("Enrolled Persons", "0", "#9b59b6")
        self._s_alerts    = StatCard("Unread Alerts",    "0", "#ef4565")
        self._s_attend    = StatCard("Today Attendance", "0", "#f39c12")
        self._s_violations= StatCard("Safety Violations","0", "#e74c3c")
        for i, card in enumerate([self._s_cameras, self._s_active,
                                   self._s_persons, self._s_alerts,
                                   self._s_attend, self._s_violations]):
            grid.addWidget(card, i // 3, i % 3)
        root.addLayout(grid)

        # Recent alerts
        root.addWidget(self._section("Recent Alerts"))
        self._alerts_list = QVBoxLayout()
        self._alerts_list.setSpacing(4)
        alerts_container = QWidget()
        alerts_container.setLayout(self._alerts_list)
        scroll = QScrollArea()
        scroll.setWidget(alerts_container)
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(180)
        scroll.setStyleSheet("border:none; background:#12121f;")
        root.addWidget(scroll)
        root.addStretch()

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size:14px; font-weight:bold; color:#9098b1;"
                          "padding-top:8px; border-top:1px solid #252550;")
        return lbl

    def _tick(self):
        self._clock.setText(datetime.now().strftime("%A, %d %B %Y  %H:%M:%S"))

    def refresh(self):
        from app.models import Camera, Person, Alert, AttendanceRecord, SafetyViolation
        from datetime import date
        session = self.db.get_session()
        try:
            self._s_cameras.set_value(str(session.query(Camera).count()))
            self._s_persons.set_value(str(session.query(Person).filter_by(is_active=True).count()))
            unread = session.query(Alert).filter_by(is_acknowledged=False).count()
            self._s_alerts.set_value(str(unread))
            today = date.today().isoformat()
            att = session.query(AttendanceRecord).filter_by(date=today).count()
            self._s_attend.set_value(str(att))
            sv = session.query(SafetyViolation).filter_by(is_resolved=False).count()
            self._s_violations.set_value(str(sv))

            # alerts list
            for i in reversed(range(self._alerts_list.count())):
                w = self._alerts_list.itemAt(i).widget()
                if w: w.deleteLater()
            alerts = session.query(Alert).order_by(
                Alert.created_at.desc()).limit(8).all()
            for a in alerts:
                row = QFrame()
                color_map = {"high": "#e74c3c", "medium": "#f39c12", "low": "#3498db"}
                color = color_map.get(a.severity, "#3498db")
                row.setStyleSheet(
                    f"border-left:4px solid {color}; background:#1b1b30;"
                    f"padding:4px 10px; border-radius:2px;"
                )
                rl = QHBoxLayout(row)
                rl.setContentsMargins(6, 4, 6, 4)
                rl.addWidget(QLabel(f"[{a.alert_type}] {a.description or ''}"))
                ts = QLabel(a.created_at.strftime("%H:%M") if a.created_at else "")
                ts.setStyleSheet("color:#666; font-size:11px;")
                rl.addStretch()
                rl.addWidget(ts)
                self._alerts_list.addWidget(row)
        finally:
            session.close()

    def set_active_streams(self, count: int):
        self._s_active.set_value(str(count))
