from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QDateEdit, QComboBox, QFileDialog
)
from PyQt6.QtCore import Qt, QDate, QTimer
from datetime import date
import csv, os

COLS = ["#", "Name", "Employee ID", "Department", "Check In", "Check Out", "Confidence"]


class AttendancePage(QWidget):
    def __init__(self, db, attendance_engine, parent=None):
        super().__init__(parent)
        self.db = db
        self.ae = attendance_engine
        self._build_ui()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(15_000)
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("Attendance")
        title.setObjectName("page_title")
        hdr.addWidget(title); hdr.addStretch()

        hdr.addWidget(QLabel("Date:"))
        self._date_edit = QDateEdit(calendarPopup=True)
        self._date_edit.setDate(QDate.currentDate())
        self._date_edit.dateChanged.connect(self.refresh)
        hdr.addWidget(self._date_edit)

        btn_export = QPushButton("Export CSV")
        btn_export.setObjectName("secondary_btn")
        btn_export.clicked.connect(self._export_csv)
        hdr.addWidget(btn_export)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.setObjectName("primary_btn")
        btn_refresh.clicked.connect(self.refresh)
        hdr.addWidget(btn_refresh)
        root.addLayout(hdr)

        # Summary row
        summary = QHBoxLayout()
        self._lbl_total   = self._chip("Total: 0")
        self._lbl_checked_in = self._chip("Checked In: 0", "#27ae60")
        self._lbl_checked_out = self._chip("Checked Out: 0", "#f39c12")
        for w in [self._lbl_total, self._lbl_checked_in, self._lbl_checked_out]:
            summary.addWidget(w)
        summary.addStretch()
        root.addLayout(summary)

        self._table = QTableWidget(0, len(COLS))
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        root.addWidget(self._table)

    def _chip(self, text: str, color: str = "#3498db") -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"background:{color}20; color:{color};"
            "border-radius:10px; padding:3px 12px; font-size:12px;"
        )
        return lbl

    def refresh(self):
        d = self._date_edit.date().toString("yyyy-MM-dd")
        session = self.db.get_session()
        try:
            records = self.ae.get_report(session, d)
        finally:
            session.close()

        self._table.setRowCount(0)
        checked_in = checked_out = 0
        for i, rec in enumerate(records):
            r = self._table.rowCount(); self._table.insertRow(r)
            for c, v in enumerate([str(i+1), rec["name"], rec["employee_id"],
                                    rec["department"], rec["check_in"],
                                    rec["check_out"], rec["confidence"]]):
                self._table.setItem(r, c, QTableWidgetItem(v))
            if rec["check_in"] != "--": checked_in += 1
            if rec["check_out"] != "--": checked_out += 1

        self._lbl_total.setText(f"Total: {len(records)}")
        self._lbl_checked_in.setText(f"Checked In: {checked_in}")
        self._lbl_checked_out.setText(f"Checked Out: {checked_out}")

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", 
            os.path.join(os.path.expanduser("~"),
                         f"attendance_{self._date_edit.date().toString('yyyyMMdd')}.csv"),
            "CSV Files (*.csv)"
        )
        if not path:
            return
        d = self._date_edit.date().toString("yyyy-MM-dd")
        session = self.db.get_session()
        try:
            records = self.ae.get_report(session, d)
        finally:
            session.close()
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "name", "employee_id", "department", "check_in", "check_out", "confidence"
            ])
            w.writeheader()
            w.writerows(records)
