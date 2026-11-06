"""Person Tracking: type a student's or staff member's name to see where AI
face recognition has seen them: now, last seen, and their movement."""
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from PyQt6.QtCore import QStringListModel, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QCompleter, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget
)

from app.core.tracking import TrackingService
from app.ui.styles import style_table

RANGES = [("Today", 0), ("Last 24 hours", 1), ("Last 7 days", 7), ("Last 30 days", 30)]


def _ago(when: datetime, now: Optional[datetime] = None) -> str:
    secs = int(((now or datetime.now()) - when).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h {secs % 3600 // 60} min ago"
    return f"{secs // 86400} days ago"


class TrackingPage(QWidget):
    live_requested = pyqtSignal(int)      # camera_id: show it in Live View

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.service = TrackingService(db)
        self._cameras: Dict[int, Dict] = {}
        self._person: Optional[Dict] = None
        self._matches: List[Dict] = []
        self._build_ui()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_status)
        self._refresh_timer.start(15000)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)
        title = QLabel("Person Tracking")
        title.setObjectName("page_title")
        root.addWidget(title)
        intro = QLabel("Type the name (or ID) of an enrolled student or staff member. "
                       "ClassGuard shows where AI face recognition has seen them. Cameras "
                       "need Face Detection or Attendance switched on.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#999;")
        root.addWidget(intro)

        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Name or ID, e.g. Riya Sharma")
        self.search.returnPressed.connect(self.track)
        self.search.textEdited.connect(self._update_completer)
        self._completer_model = QStringListModel()
        completer = QCompleter(self._completer_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.activated.connect(lambda _t: self.track())
        self.search.setCompleter(completer)
        row.addWidget(self.search, 1)
        self.range_cb = QComboBox()
        for label, days in RANGES:
            self.range_cb.addItem(label, days)
        self.range_cb.currentIndexChanged.connect(lambda _i: self._person and self._load())
        row.addWidget(self.range_cb)
        btn = QPushButton("Track")
        btn.setObjectName("primary_btn")
        btn.clicked.connect(self.track)
        row.addWidget(btn)
        root.addLayout(row)

        self.matches = QListWidget()
        self.matches.setMaximumHeight(90)
        self.matches.setVisible(False)
        self.matches.itemClicked.connect(
            lambda it: self.select_person(it.data(Qt.ItemDataRole.UserRole)))
        root.addWidget(self.matches)

        card = QHBoxLayout()
        self.photo = QLabel()
        self.photo.setFixedSize(96, 96)
        self.photo.setStyleSheet("background:#151528; border-radius:6px;")
        self.photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.addWidget(self.photo)
        info = QVBoxLayout()
        self.name_lbl = QLabel("No one selected")
        self.name_lbl.setStyleSheet("font-size:18px; font-weight:bold;")
        self.meta_lbl = QLabel()
        self.meta_lbl.setStyleSheet("color:#999;")
        self.now_lbl = QLabel()
        self.now_lbl.setWordWrap(True)
        self.now_lbl.setStyleSheet("font-size:14px;")
        info.addWidget(self.name_lbl)
        info.addWidget(self.meta_lbl)
        info.addWidget(self.now_lbl)
        info.addStretch()
        card.addLayout(info, 1)
        self.live_btn = QPushButton("Show camera live")
        self.live_btn.setEnabled(False)
        self.live_btn.clicked.connect(self._show_live)
        card.addWidget(self.live_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(card)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("Movement (stays at each camera)"))
        self.visits = QTableWidget(0, 4)
        style_table(self.visits)
        self.visits.setHorizontalHeaderLabels(["From", "To", "Camera", "Location"])
        self.visits.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.visits.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.visits.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.visits.itemSelectionChanged.connect(self._show_visit_picture)
        ll.addWidget(self.visits)
        split.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("Picture"))
        self.visit_pic = QLabel("Select a row")
        self.visit_pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.visit_pic.setMinimumSize(200, 200)
        self.visit_pic.setStyleSheet("background:#151528; color:#666;")
        rl.addWidget(self.visit_pic, 1)
        split.addWidget(right)
        split.setSizes([700, 260])
        root.addWidget(split, 1)
        self._visit_rows: List[Dict] = []

    # ----------------------------------------------------------------- data
    def set_cameras(self, cameras: List[Dict]):
        self._cameras = {c["id"]: c for c in cameras}

    def _cam_name(self, cid) -> str:
        return self._cameras.get(cid, {}).get("name", f"Camera {cid}")

    def _update_completer(self, text: str):
        if len(text.strip()) >= 1:
            self._completer_model.setStringList(
                [p["name"] for p in self.service.find_people(text, limit=15)])

    def track(self):
        text = self.search.text().strip()
        if not text:
            return
        people = self.service.find_people(text)
        exact = [p for p in people if p["name"].lower() == text.lower()
                 or (p["employee_id"] and p["employee_id"].lower() == text.lower())]
        self.matches.clear()
        if exact or len(people) == 1:
            self.matches.setVisible(False)
            self.select_person((exact or people)[0])
        elif people:
            for p in people:
                it = QListWidgetItem(f"{p['name']}  ·  {p['role'] or ''} {p['department'] or ''}"
                                     .strip(" ·"))
                it.setData(Qt.ItemDataRole.UserRole, p)
                self.matches.addItem(it)
            self.matches.setVisible(True)
            self.name_lbl.setText(f"{len(people)} people match — pick one")
        else:
            self.matches.setVisible(False)
            self._person = None
            self.name_lbl.setText("No enrolled person with that name")
            self.meta_lbl.setText("Enrol them in Face Enrollment first.")
            self.now_lbl.setText("")
            self.visits.setRowCount(0)

    def select_person(self, person: Dict):
        self._person = person
        self.matches.setVisible(False)
        self.search.setText(person["name"])
        self.name_lbl.setText(person["name"])
        self.meta_lbl.setText("  ·  ".join(x for x in (person.get("role"), person.get("department"),
                                                      person.get("employee_id")) if x))
        pix = QPixmap(person.get("photo") or "")
        self.photo.setPixmap(pix.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)
                             if not pix.isNull() else QPixmap())
        if pix.isNull():
            self.photo.setText("No photo")
        self._load()

    def _load(self):
        if not self._person:
            return
        days = self.range_cb.currentData()
        now = datetime.now()
        since = now.replace(hour=0, minute=0, second=0, microsecond=0) if days == 0 \
            else now - timedelta(days=days)
        rows = self.service.visits(self.service.sightings(self._person["id"], since))
        rows.reverse()                                   # newest first
        self._visit_rows = rows
        self.visits.setRowCount(0)
        for v in rows:
            r = self.visits.rowCount()
            self.visits.insertRow(r)
            same_day = v["start"].date() == now.date()
            fmt = "%H:%M:%S" if same_day else "%d %b %H:%M"
            cam = self._cameras.get(v["camera_id"], {})
            for c, val in enumerate([v["start"].strftime(fmt), v["end"].strftime(fmt),
                                     self._cam_name(v["camera_id"]), cam.get("location", "")]):
                self.visits.setItem(r, c, QTableWidgetItem(val))
        self._refresh_status()

    def _refresh_status(self):
        if not self._person:
            return
        last = self.service.last_seen(self._person["id"])
        self._last = last
        if not last:
            self.now_lbl.setText("Not seen by any camera yet.")
            self.now_lbl.setStyleSheet("font-size:14px; color:#999;")
            self.live_btn.setEnabled(False)
            return
        cam = self._cam_name(last["camera_id"])
        loc = self._cameras.get(last["camera_id"], {}).get("location")
        where = f"{cam}" + (f" ({loc})" if loc else "")
        if self.service.is_now(last["time"]):
            self.now_lbl.setText(f"● Visible now on {where}")
            self.now_lbl.setStyleSheet("font-size:14px; color:#27ae60; font-weight:bold;")
        else:
            self.now_lbl.setText(f"Last seen on {where} at {last['time']:%d %b %H:%M:%S} "
                                 f"({_ago(last['time'])})")
            self.now_lbl.setStyleSheet("font-size:14px; color:#dde1ec;")
        self.live_btn.setEnabled(last["camera_id"] in self._cameras)

    def _show_live(self):
        last = getattr(self, "_last", None)
        if last:
            self.live_requested.emit(last["camera_id"])

    def _show_visit_picture(self):
        rows = self.visits.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self._visit_rows):
            return
        path = self._visit_rows[rows[0].row()].get("snapshot") or ""
        pix = QPixmap(path)
        if pix.isNull():
            self.visit_pic.setText("No picture")
        else:
            self.visit_pic.setPixmap(pix.scaled(self.visit_pic.size(),
                                                Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation))

    # ------------------------------------------------------------- live feed
    def on_person_seen(self, seen: Dict):
        """Live update while someone is being tracked."""
        if self._person and seen.get("person_id") == self._person["id"]:
            self._load()

    def tracked_person_id(self) -> Optional[int]:
        return self._person["id"] if self._person else None

    def refresh(self):
        self._load()

