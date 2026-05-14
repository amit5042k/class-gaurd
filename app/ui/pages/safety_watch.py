import cv2
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QSplitter, QGroupBox, QCheckBox, QFileDialog, QGridLayout
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap
from app.core.safety_engine import SafetyEngine
from app.core.stream_manager import StreamManager
from typing import List, Dict
from datetime import datetime


class SafetyWatch(QWidget):
    def __init__(self, db, stream_manager: StreamManager, parent=None):
        super().__init__(parent)
        self.db = db
        self.sm = stream_manager
        self.se = SafetyEngine()
        self._cameras: List[Dict] = []
        self._active_cam_id: int = None
        self._current_frame = None
        self._build_ui()
        self._timer = QTimer()
        self._timer.timeout.connect(self._process)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("Safety Watch")
        title.setObjectName("page_title")
        hdr.addWidget(title); hdr.addStretch()
        hdr.addWidget(QLabel("Camera:"))
        self._cam_cb = QComboBox()
        self._cam_cb.currentIndexChanged.connect(self._switch_camera)
        hdr.addWidget(self._cam_cb)
        self._btn_toggle = QPushButton("Start Safety Watch")
        self._btn_toggle.setObjectName("primary_btn")
        self._btn_toggle.clicked.connect(self._toggle)
        hdr.addWidget(self._btn_toggle)
        root.addLayout(hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: video + controls
        left = QWidget()
        ll = QVBoxLayout(left)
        self._video_lbl = QLabel("No stream")
        self._video_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_lbl.setStyleSheet("background:#09090f; border:1px solid #252550;")
        self._video_lbl.setMinimumHeight(360)
        ll.addWidget(self._video_lbl)

        gb = QGroupBox("YOLO Model (optional)")
        gl = QHBoxLayout(gb)
        self._model_lbl = QLabel("Not loaded — using person detector")
        self._model_lbl.setStyleSheet("color:#666; font-size:11px;")
        btn_load = QPushButton("Load YOLO Weights")
        btn_load.setObjectName("secondary_btn")
        btn_load.clicked.connect(self._load_yolo)
        gl.addWidget(self._model_lbl); gl.addStretch(); gl.addWidget(btn_load)
        ll.addWidget(gb)
        splitter.addWidget(left)

        # Right: violations log
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("Violation Log"))
        self._vio_table = QTableWidget(0, 5)
        self._vio_table.setHorizontalHeaderLabels(
            ["Time", "Camera", "Type", "Confidence", "Zone"]
        )
        self._vio_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._vio_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._vio_table.setAlternatingRowColors(True)
        rl.addWidget(self._vio_table)

        # Stats
        stats = QHBoxLayout()
        self._lbl_detections = self._stat_lbl("Detections: 0")
        self._lbl_violations = self._stat_lbl("Violations: 0", "#ef4565")
        self._lbl_intrusions = self._stat_lbl("Zone Intrusions: 0", "#f39c12")
        for w in [self._lbl_detections, self._lbl_violations, self._lbl_intrusions]:
            stats.addWidget(w)
        stats.addStretch()
        rl.addLayout(stats)
        splitter.addWidget(right)
        splitter.setSizes([580, 340])
        root.addWidget(splitter)

        self._total_det = self._total_vio = self._total_intr = 0

    def _stat_lbl(self, text: str, color: str = "#3498db") -> QLabel:
        l = QLabel(text)
        l.setStyleSheet(
            f"background:{color}20; color:{color};"
            "border-radius:10px; padding:3px 12px; font-size:12px;"
        )
        return l

    def set_cameras(self, cameras: List[Dict]):
        self._cameras = cameras
        self._cam_cb.clear()
        for c in cameras:
            self._cam_cb.addItem(c["name"], c["id"])

    def _switch_camera(self, idx: int):
        if 0 <= idx < len(self._cameras):
            self._active_cam_id = self._cameras[idx]["id"]

    def _toggle(self):
        if self._timer.isActive():
            self._timer.stop()
            self._btn_toggle.setText("Start Safety Watch")
            self._btn_toggle.setObjectName("primary_btn")
        else:
            if not self._active_cam_id and self._cameras:
                self._active_cam_id = self._cameras[0]["id"]
            self._timer.start(400)
            self._btn_toggle.setText("Stop Safety Watch")
            self._btn_toggle.setObjectName("danger_btn")

    def receive_frame(self, camera_id: int, frame):
        if camera_id == self._active_cam_id:
            self._current_frame = frame.copy()

    def _process(self):
        if self._current_frame is None:
            return
        frame = self._current_frame.copy()
        detections = self.se.detect(frame)
        annotated = self.se.draw(frame, detections)

        rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        img = QImage(rgb.data.tobytes(), w, h, ch*w, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(img).scaled(
            self._video_lbl.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self._video_lbl.setPixmap(pix)

        cam_name = next((c["name"] for c in self._cameras
                         if c["id"] == self._active_cam_id), "Unknown")
        self._total_det += len(detections)
        for d in detections:
            if d.get("violation") or d.get("zone_intrusion"):
                self._log_violation(d, cam_name)
                if d.get("violation"): self._total_vio += 1
                if d.get("zone_intrusion"): self._total_intr += 1

        self._lbl_detections.setText(f"Detections: {self._total_det}")
        self._lbl_violations.setText(f"Violations: {self._total_vio}")
        self._lbl_intrusions.setText(f"Zone Intrusions: {self._total_intr}")

    def _log_violation(self, d: Dict, cam_name: str):
        r = self._vio_table.rowCount()
        if r > 500:
            self._vio_table.removeRow(0)
            r = self._vio_table.rowCount()
        self._vio_table.insertRow(r)
        zone = "Yes" if d.get("zone_intrusion") else "No"
        for c, v in enumerate([
            datetime.now().strftime("%H:%M:%S"), cam_name,
            d.get("class",""), f"{d['confidence']:.0%}", zone
        ]):
            item = QTableWidgetItem(v)
            if d.get("violation") or d.get("zone_intrusion"):
                item.setForeground(Qt.GlobalColor.red)
            self._vio_table.setItem(r, c, item)
        self._vio_table.scrollToBottom()

    def _load_yolo(self):
        weights, _ = QFileDialog.getOpenFileName(
            self, "Select YOLO Weights", "", "Weights (*.weights *.pt)"
        )
        if not weights: return
        cfg, _ = QFileDialog.getOpenFileName(
            self, "Select YOLO Config", "", "Config (*.cfg)"
        )
        if not cfg: return
        names, _ = QFileDialog.getOpenFileName(
            self, "Select Class Names", "", "Names (*.names *.txt)"
        )
        if not names: return
        ok = self.se.load_yolo(weights, cfg, names)
        self._model_lbl.setText(
            "YOLO loaded!" if ok else "Failed to load YOLO"
        )
