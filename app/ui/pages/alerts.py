from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QComboBox, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

COLS = ["ID", "Camera", "Type", "Description", "Severity", "Time", "Actions"]
SEVERITY_COLORS = {"high": "#e74c3c", "medium": "#f39c12", "low": "#3498db"}


class AlertsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(10_000)
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("Alerts")
        title.setObjectName("page_title")
        hdr.addWidget(title); hdr.addStretch()
        hdr.addWidget(QLabel("Filter:"))
        self._filter = QComboBox()
        self._filter.addItems(["All", "Unread", "High", "Medium", "Low"])
        self._filter.currentIndexChanged.connect(self.refresh)
        hdr.addWidget(self._filter)
        btn_ack_all = QPushButton("Acknowledge All")
        btn_ack_all.setObjectName("secondary_btn")
        btn_ack_all.clicked.connect(self._ack_all)
        hdr.addWidget(btn_ack_all)
        root.addLayout(hdr)

        self._table = QTableWidget(0, len(COLS))
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        root.addWidget(self._table)

    def refresh(self):
        from app.models import Alert, Camera
        session = self.db.get_session()
        try:
            q = session.query(Alert)
            f = self._filter.currentText()
            if f == "Unread":   q = q.filter_by(is_acknowledged=False)
            elif f == "High":   q = q.filter_by(severity="high")
            elif f == "Medium": q = q.filter_by(severity="medium")
            elif f == "Low":    q = q.filter_by(severity="low")
            alerts = q.order_by(Alert.created_at.desc()).limit(200).all()
            cam_names = {}
            for cam in session.query(Camera).all():
                cam_names[cam.id] = cam.name

            self._table.setRowCount(0)
            for a in alerts:
                r = self._table.rowCount(); self._table.insertRow(r)
                cam_name = cam_names.get(a.camera_id, "--") if a.camera_id else "--"
                ts = a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else ""
                color = SEVERITY_COLORS.get(a.severity, "#aaa")
                for c, v in enumerate([str(a.id), cam_name, a.alert_type or "",
                                        a.description or "", a.severity or "", ts]):
                    item = QTableWidgetItem(v)
                    if not a.is_acknowledged:
                        item.setBackground(QColor(color + "22"))
                    self._table.setItem(r, c, item)

                act = QWidget(); al = QHBoxLayout(act); al.setContentsMargins(4,2,4,2)
                aid = a.id
                ack_btn = QPushButton("Ack")
                ack_btn.setObjectName("secondary_btn"); ack_btn.setFixedWidth(44)
                ack_btn.setEnabled(not a.is_acknowledged)
                ack_btn.clicked.connect(lambda _, i=aid: self._acknowledge(i))
                al.addWidget(ack_btn)
                self._table.setCellWidget(r, len(COLS)-1, act)
        finally:
            session.close()

    def _acknowledge(self, alert_id: int):
        from app.models import Alert
        session = self.db.get_session()
        try:
            a = session.query(Alert).get(alert_id)
            if a: a.is_acknowledged = True; session.commit()
        finally:
            session.close()
        self.refresh()

    def _ack_all(self):
        from app.models import Alert
        session = self.db.get_session()
        try:
            session.query(Alert).filter_by(is_acknowledged=False).update(
                {"is_acknowledged": True}
            )
            session.commit()
        finally:
            session.close()
        self.refresh()
