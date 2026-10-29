import importlib.util
from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)

from app.core.expression_engine import EMOTION_COLORS
from app.ui.widgets.zone_canvas import VideoCanvas
from app.ui.styles import style_table

EMOTIONS = ["happy", "neutral", "sad", "angry", "fear", "surprise", "disgust"]


def _hex(bgr) -> str:
    b, g, r = bgr
    return f"#{r:02x}{g:02x}{b:02x}"


class ExpressionMonitor(QWidget):
    """Displays emotion results produced by the background AI worker."""

    monitor_changed = pyqtSignal(str, object)  # "expression", camera_id or None

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._cameras: List[Dict] = []
        self._active: Optional[int] = None
        self._counter: Counter = Counter()
        self._model_ok = importlib.util.find_spec("deepface") is not None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("Expression Monitor")
        title.setObjectName("page_title")
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(QLabel("Camera:"))
        self._cam_cb = QComboBox()
        self._cam_cb.setMinimumWidth(180)
        hdr.addWidget(self._cam_cb)
        self._btn = QPushButton("Start Monitoring")
        self._btn.setObjectName("primary_btn")
        self._btn.clicked.connect(self._toggle)
        hdr.addWidget(self._btn)
        root.addLayout(hdr)

        if not self._model_ok:
            note = QLabel("The emotion model (DeepFace) is not installed, so faces are "
                          "outlined but emotions are not read. Install it with: "
                          "pip install -r requirements-ai.txt")
            note.setWordWrap(True)
            note.setStyleSheet("background:#3a2a10; color:#f3c068; padding:8px; border-radius:4px;")
            root.addWidget(note)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self._video = VideoCanvas("Select a camera and click Start Monitoring")
        ll.addWidget(self._video, 1)
        self._bars: Dict[str, QProgressBar] = {}
        for emo in EMOTIONS:
            row = QHBoxLayout()
            name = QLabel(emo.capitalize())
            name.setFixedWidth(70)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setFixedHeight(14)
            bar.setFormat("%p%")
            bar.setStyleSheet(f"QProgressBar::chunk{{background:{_hex(EMOTION_COLORS[emo])};}}")
            self._bars[emo] = bar
            row.addWidget(name)
            row.addWidget(bar)
            ll.addLayout(row)
        splitter.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("Expression log"))
        self._log = QTableWidget(0, 3)
        style_table(self._log)
        self._log.setHorizontalHeaderLabels(["Time", "Emotion", "Confidence"])
        self._log.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._log.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._log.setAlternatingRowColors(True)
        rl.addWidget(self._log)
        splitter.addWidget(right)
        splitter.setSizes([620, 320])
        root.addWidget(splitter, 1)

    # --------------------------------------------------------------- inputs
    def set_cameras(self, cameras: List[Dict]):
        current = self._cam_cb.currentData()
        self._cameras = cameras
        self._cam_cb.blockSignals(True)
        self._cam_cb.clear()
        for c in cameras:
            self._cam_cb.addItem(c["name"], c["id"])
        idx = self._cam_cb.findData(current)
        self._cam_cb.setCurrentIndex(max(idx, 0))
        self._cam_cb.blockSignals(False)
        if self._active is not None and self._active not in {c["id"] for c in cameras}:
            self._stop()

    def _toggle(self):
        if self._active is None:
            cid = self._cam_cb.currentData()
            if cid is None:
                self._video.clear_image("Add a camera first (Camera Management).")
                return
            self._active = cid
            self._counter.clear()
            self._cam_cb.setEnabled(False)
            self._btn.setText("Stop Monitoring")
            self._video.clear_image("Connecting to the camera…")
            self.monitor_changed.emit("expression", cid)
        else:
            self._stop()

    def _stop(self):
        self._active = None
        self._cam_cb.setEnabled(True)
        self._btn.setText("Start Monitoring")
        self._video.clear_image("Monitoring stopped")
        self.monitor_changed.emit("expression", None)

    def on_frame(self, kind: str, camera_id: int, img: QImage):
        if kind == "expression" and camera_id == self._active:
            self._video.set_image(img)

    def on_results(self, camera_id: int, results: list):
        if camera_id != self._active or not self._model_ok:
            return
        for r in results:
            emo = r.get("dominant_emotion")
            if emo in self._bars:
                self._counter[emo] += 1
                self._add_log(emo, r.get("confidence", 0.0))
        total = sum(self._counter.values()) or 1
        for emo, bar in self._bars.items():
            bar.setValue(int(100 * self._counter.get(emo, 0) / total))

    def _add_log(self, emotion: str, confidence: float):
        if self._log.rowCount() >= 300:
            self._log.removeRow(0)
        r = self._log.rowCount()
        self._log.insertRow(r)
        for c, v in enumerate([datetime.now().strftime("%H:%M:%S"),
                               emotion.capitalize(), f"{confidence:.0f}%"]):
            self._log.setItem(r, c, QTableWidgetItem(v))
        self._log.scrollToBottom()
