import os
import shutil
import subprocess
import sys
from datetime import datetime

from PyQt6.QtCore import QDate, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox, QDateEdit, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget
)

from app.config import Config
from app.core.recording import _free_bytes, recordings_for_day
from app.core.security import audit
from app.ui.styles import style_table
from app.ui.widgets.video_player import KIND_COLORS, DayTimeline, VideoPlayer, fmt_secs

COLS = ["Start", "End", "Length", "Type", "Size"]


class RecordingsPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._cameras = []
        self._rows = []
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(8)
        title = QLabel("Recordings")
        title.setObjectName("page_title")
        outer.addWidget(title)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        local = QWidget()
        self.tabs.addTab(local, "Recorded by ClassGuard (this PC)")
        from app.ui.pages.nvr_playback import NVRPlaybackTab
        self.nvr = NVRPlaybackTab()
        self.tabs.addTab(self.nvr, "Recorded on the NVR / camera")
        root = QVBoxLayout(local)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.addStretch()
        hdr.addWidget(QLabel("Camera:"))
        self._cam_cb = QComboBox()
        self._cam_cb.setMinimumWidth(180)
        self._cam_cb.currentIndexChanged.connect(self.refresh)
        hdr.addWidget(self._cam_cb)
        hdr.addWidget(QLabel("Date:"))
        self._date = QDateEdit(calendarPopup=True)
        self._date.setDisplayFormat("dd MMM yyyy")
        self._date.setDate(QDate.currentDate())
        self._date.dateChanged.connect(self.refresh)
        hdr.addWidget(self._date)
        self._kind = QComboBox()
        self._kind.addItem("All types", "")
        for k in ("continuous", "motion", "manual"):
            self._kind.addItem(k.capitalize(), k)
        self._kind.currentIndexChanged.connect(self.refresh)
        hdr.addWidget(self._kind)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.setObjectName("secondary_btn")
        btn_refresh.clicked.connect(self.refresh)
        hdr.addWidget(btn_refresh)
        root.addLayout(hdr)

        self._timeline = DayTimeline()
        self._timeline.time_clicked.connect(self._play_at)
        root.addWidget(self._timeline)
        legend = QHBoxLayout()
        for k, c in KIND_COLORS.items():
            lbl = QLabel(f"■ {k.capitalize()}")
            lbl.setStyleSheet(f"color:{c}; font-size:11px;")
            legend.addWidget(lbl)
        legend.addStretch()
        self._disk = QLabel()
        self._disk.setStyleSheet("color:#777; font-size:11px;")
        legend.addWidget(self._disk)
        root.addLayout(legend)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self._table = QTableWidget(0, len(COLS))
        style_table(self._table)
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setMinimumWidth(430)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.cellDoubleClicked.connect(lambda r, _c: self._play_row(r))
        ll.addWidget(self._table)
        btns = QHBoxLayout()
        self._btn_export = QPushButton("Export…")
        self._btn_export.setObjectName("secondary_btn")
        self._btn_export.clicked.connect(self._export)
        btn_folder = QPushButton("Open folder")
        btn_folder.setObjectName("secondary_btn")
        btn_folder.clicked.connect(self._open_folder)
        self._btn_delete = QPushButton("Delete")
        self._btn_delete.setObjectName("danger_btn")
        self._btn_delete.clicked.connect(self._delete)
        for b in (self._btn_export, btn_folder, self._btn_delete):
            btns.addWidget(b)
        btns.addStretch()
        ll.addLayout(btns)
        split.addWidget(left)
        self.player = VideoPlayer()
        self.player.snapshot_saved.connect(
            lambda p: QMessageBox.information(self, "Snapshot", f"Saved to\n{p}"))
        split.addWidget(self.player)
        split.setSizes([470, 710])
        root.addWidget(split, 1)

    # --------------------------------------------------------------- data
    def set_cameras(self, cameras):
        current = self._cam_cb.currentData()
        self._cameras = cameras
        self._cam_cb.blockSignals(True)
        self._cam_cb.clear()
        for c in cameras:
            self._cam_cb.addItem(c["name"], c["id"])
        idx = self._cam_cb.findData(current)
        self._cam_cb.setCurrentIndex(max(idx, 0))
        self._cam_cb.blockSignals(False)
        self.nvr.set_cameras(cameras)
        self.refresh()

    def refresh(self):
        cid = self._cam_cb.currentData()
        day = self._date.date().toPyDate()
        rows = recordings_for_day(self.db, cid, day) if cid is not None else []
        self._timeline.set_data(day, rows)
        kind = self._kind.currentData()
        self._rows = [r for r in rows if not kind or r["kind"] == kind]
        self._table.setRowCount(0)
        for r in self._rows:
            i = self._table.rowCount()
            self._table.insertRow(i)
            end = r["end"]
            length = fmt_secs((end - r["start"]).total_seconds()) if end else "recording…"
            vals = [r["start"].strftime("%H:%M:%S"), end.strftime("%H:%M:%S") if end else "—",
                    length, r["kind"].capitalize(), f"{r['size'] / 1_048_576:.1f} MB"]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c == 3:
                    item.setForeground(Qt.GlobalColor.white)
                    item.setBackground(_qcolor(KIND_COLORS.get(r["kind"], "#888"), 90))
                self._table.setItem(i, c, item)
        free = _free_bytes(Config.RECORDINGS_DIR) / 1024 ** 3
        self._disk.setText(f"Keeping {Config.RECORDING_RETENTION_DAYS} days, up to "
                           f"{Config.RECORDING_MAX_GB:g} GB · {free:.0f} GB free on disk")

    def _selected(self):
        rows = sorted({i.row() for i in self._table.selectedItems()})
        return [self._rows[r] for r in rows if r < len(self._rows)]

    def _play_row(self, row: int, offset: float = 0.0):
        if row >= len(self._rows):
            return
        r = self._rows[row]
        if r["end"] is None:
            QMessageBox.information(self, "Still recording",
                                    "This file is still being recorded. It can be played "
                                    "once the segment finishes.")
            return
        self.player.open(r["path"], r["start"], offset)
        self._timeline.set_marker(r["start"])

    def _play_at(self, when: datetime):
        for i, r in enumerate(self._rows):
            if r["end"] and r["start"] <= when <= r["end"]:
                self._table.selectRow(i)
                self._play_row(i, (when - r["start"]).total_seconds())
                self._timeline.set_marker(when)
                return
        later = [i for i, r in enumerate(self._rows) if r["start"] > when and r["end"]]
        if later:
            self._table.selectRow(later[0])
            self._play_row(later[0])

    def open_nvr_playback(self, camera_id: int, when: datetime) -> bool:
        """Play the recorder's own recording of a camera at `when`."""
        if not self.nvr.has_camera(camera_id):
            return False
        self.tabs.setCurrentWidget(self.nvr)
        return self.nvr.open_camera_at(camera_id, when)

    def open_recording(self, camera_id: int, path: str, start: datetime, offset: float):
        """Called from an alert: show that camera/day and play from `offset`."""
        idx = self._cam_cb.findData(camera_id)
        if idx >= 0:
            self._cam_cb.setCurrentIndex(idx)
        self._date.setDate(QDate(start.year, start.month, start.day))
        self.refresh()
        for i, r in enumerate(self._rows):
            if r["path"] == path:
                self._table.selectRow(i)
        self.player.open(path, start, offset)
        self._timeline.set_marker(start)

    # ------------------------------------------------------------ actions
    def set_permissions(self, user):
        self._btn_export.setEnabled(user.can_operate)
        self._btn_delete.setEnabled(user.is_admin)

    def _export(self):
        sel = self._selected()
        if not sel:
            QMessageBox.information(self, "Export", "Select a recording first.")
            return
        r = sel[0]
        dest, _ = QFileDialog.getSaveFileName(self, "Export recording",
                                              os.path.basename(r["path"]), "MP4 video (*.mp4)")
        if dest:
            shutil.copyfile(r["path"], dest)
            audit(self.db, "recording_export", f"{os.path.basename(r['path'])} -> {dest}")
            QMessageBox.information(self, "Exported", f"Saved to\n{dest}")

    def _delete(self):
        sel = [r for r in self._selected() if r["end"] is not None]
        if not sel:
            return
        if QMessageBox.question(self, "Delete", f"Delete {len(sel)} recording(s)?",
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        from app.models import Recording
        if self.player.path in {r["path"] for r in sel}:
            self.player.close_file()
        s = self.db.get_session()
        try:
            for r in sel:
                try:
                    os.remove(r["path"])
                except OSError:
                    pass
                s.query(Recording).filter_by(id=r["id"]).delete()
            s.commit()
        finally:
            s.close()
        audit(self.db, "recording_delete", ", ".join(os.path.basename(r["path"]) for r in sel))
        self.refresh()

    def _open_folder(self):
        folder = Config.RECORDINGS_DIR
        os.makedirs(folder, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", folder], check=False)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


def _qcolor(hex_color: str, alpha: int):
    from PyQt6.QtGui import QColor
    c = QColor(hex_color)
    c.setAlpha(alpha)
    return c
