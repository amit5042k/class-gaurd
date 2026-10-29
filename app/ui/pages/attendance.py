import csv
import os
from datetime import date

from PyQt6.QtCore import QDate, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDateEdit, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from app.config import Config
from app.core.attendance_engine import AttendanceEngine
from app.ui.styles import style_table

COLS = ["Name", "Employee ID", "Department", "Status",
        "Check In", "Last Seen", "Hours", "Match"]
STATUS_COLORS = {"Present": "#27ae60", "Late": "#f39c12", "Absent": "#ef4565"}


class AttendancePage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(30_000)
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("Attendance")
        title.setObjectName("page_title")
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(QLabel("Date:"))
        self._date = QDateEdit(calendarPopup=True)
        self._date.setDisplayFormat("dd MMM yyyy")
        self._date.setDate(QDate.currentDate())
        self._date.dateChanged.connect(self.refresh)
        hdr.addWidget(self._date)
        btn_day = QPushButton("Export day (CSV)")
        btn_day.setObjectName("secondary_btn")
        btn_day.clicked.connect(self._export_day)
        btn_month = QPushButton("Export month (CSV)")
        btn_month.setObjectName("secondary_btn")
        btn_month.clicked.connect(self._export_month)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.setObjectName("primary_btn")
        btn_refresh.clicked.connect(self.refresh)
        for b in (btn_day, btn_month, btn_refresh):
            hdr.addWidget(b)
        root.addLayout(hdr)

        summary = QHBoxLayout()
        self._chips = {}
        for key, color, rgb in (("Present", "#27ae60", "39,174,96"),
                                ("Late", "#f39c12", "243,156,18"),
                                ("Absent", "#ef4565", "239,69,101")):
            lbl = QLabel()
            lbl.setStyleSheet(f"background:rgba({rgb},0.15); color:{color}; border-radius:10px;"
                              "padding:3px 12px; font-size:12px;")
            self._chips[key] = lbl
            summary.addWidget(lbl)
        self._shift_lbl = QLabel()
        self._shift_lbl.setStyleSheet("color:#777; font-size:11px;")
        summary.addStretch()
        summary.addWidget(self._shift_lbl)
        root.addLayout(summary)

        self._table = QTableWidget(0, len(COLS))

        style_table(self._table)
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        root.addWidget(self._table)

    def _date_str(self) -> str:
        return self._date.date().toString("yyyy-MM-dd")

    def _rows(self, date_str: str):
        session = self.db.get_session()
        try:
            return AttendanceEngine.get_report(session, date_str)
        finally:
            session.close()

    def refresh(self):
        rows = self._rows(self._date_str())
        self._table.setRowCount(0)
        counts = {"Present": 0, "Late": 0, "Absent": 0}
        for rec in rows:
            counts[rec["status"]] += 1
            r = self._table.rowCount()
            self._table.insertRow(r)
            for c, v in enumerate([rec["name"], rec["employee_id"],
                                   rec["department"], rec["status"], rec["check_in"],
                                   rec["check_out"], rec["hours"], rec["confidence"]]):
                item = QTableWidgetItem(v)
                if c == 3:
                    item.setForeground(QColor(STATUS_COLORS[rec["status"]]))
                self._table.setItem(r, c, item)
        for key, lbl in self._chips.items():
            lbl.setText(f"{key}: {counts[key]}")
        days = ", ".join(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d]
                         for d in Config.WEEKLY_OFF_DAYS) or "none"
        self._shift_lbl.setText(f"Shift starts {Config.SHIFT_START}, late after "
                                f"{Config.LATE_GRACE_MINUTES} min · Weekly off: {days} "
                                "(change in Settings)")

    def on_attendance_event(self, event: dict):
        if event.get("type") == "check_in" and self._date_str() == date.today().isoformat():
            self.refresh()

    def _ask_path(self, default_name: str) -> str:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save CSV", os.path.join(os.path.expanduser("~"), default_name),
            "CSV Files (*.csv)")
        return path

    def _export_day(self):
        path = self._ask_path(f"attendance_{self._date_str()}.csv")
        if not path:
            return
        rows = self._rows(self._date_str())
        fields = ["name", "employee_id", "department", "status",
                  "check_in", "check_out", "hours", "confidence"]
        # utf-8-sig so Excel shows non-English names correctly
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writerow({"check_out": "last_seen", **{k: k for k in fields if k != "check_out"}})
            w.writerows(rows)
        QMessageBox.information(self, "Exported", f"Saved {len(rows)} rows to\n{path}")

    def _export_month(self):
        d = self._date.date()
        path = self._ask_path(f"attendance_{d.year()}_{d.month():02d}.csv")
        if not path:
            return
        session = self.db.get_session()
        try:
            days, rows = AttendanceEngine.get_monthly_report(session, d.year(), d.month())
        finally:
            session.close()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Name", "Employee ID", "Department", "Present", "Late",
                        "Absent", "Hours"] + [str(n) for n in days])
            for r in rows:
                w.writerow([r["name"], r["employee_id"], r["department"], r["present"],
                            r["late"], r["absent"], r["hours"]] + r["days"])
            w.writerow([])
            w.writerow(["Key: P present, L late, A absent, O weekly off"])
        QMessageBox.information(self, "Exported",
                                f"Saved the {d.toString('MMMM yyyy')} report to\n{path}")

