import cv2
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QSplitter, QScrollArea
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap
from app.core.expression_engine import ExpressionEngine, EMOTION_COLORS
from app.core.stream_manager import StreamManager
from typing import List, Dict
from collections import Counter
from datetime import datetime


class ExpressionMonitor(QWidget):
    def __init__(self, db, stream_manager: StreamManager, parent=None):
        super().__init__(parent)
        self.db = db
        self.sm = stream_manager
        self.ee = ExpressionEngine()
        self._cameras: List[Dict] = []
        self._active_cam_id: int = None
        self._current_frame = None
        self._emotion_counter: Counter = Counter()
        self._log: List[Dict] = []
        self._build_ui()
        self._analysis_timer = QTimer()
        self._analysis_timer.timeout.connect(self._analyze)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("Expression Monitor")
        title.setObjectName("page_title")
        hdr.addWidget(title); hdr.addStretch()
        hdr.addWidget(QLabel("Camera:"))
        self._cam_cb = QComboBox()
        self._cam_cb.currentIndexChanged.connect(self._switch_camera)
        hdr.addWidget(self._cam_cb)
        self._btn_toggle = QPushButton("Start Monitoring")
        self._btn_toggle.setObjectName("primary_btn")
        self._btn_toggle.clicked.connect(self._toggle)
        hdr.addWidget(self._btn_toggle)
        root.addLayout(hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: live feed
        left = QWidget()
        ll = QVBoxLayout(left)
        self._video_lbl = QLabel("No stream")
        self._video_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_lbl.setStyleSheet("background:#09090f; border:1px solid #252550;")
        self._video_lbl.setMinimumHeight(360)
        ll.addWidget(self._video_lbl)

        # Emotion bars
        self._bars: Dict[str, QLabel] = {}
        for emo in ["happy", "neutral", "sad", "angry", "fear", "surprise", "disgust"]:
            row = QHBoxLayout()
            name = QLabel(emo.capitalize())
            name.setFixedWidth(70)
            name.setStyleSheet("font-size:11px;")
            bar = QLabel()
            bar.setFixedHeight(12)
            bar.setStyleSheet("background:#252550; border-radius:6px;")
            self._bars[emo] = bar
            row.addWidget(name); row.addWidget(bar)
            ll.addLayout(row)
        splitter.addWidget(left)

        # Right: log table
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("Expression Log"))
        self._log_table = QTableWidget(0, 4)
        self._log_table.setHorizontalHeaderLabels(["Time", "Camera", "Emotion", "Confidence"])
        self._log_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._log_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._log_table.setAlternatingRowColors(True)
        rl.addWidget(self._log_table)
        splitter.addWidget(right)
        splitter.setSizes([580, 340])
        root.addWidget(splitter)

    def set_cameras(self, cameras: List[Dict]):
        self._cameras = cameras
        self._cam_cb.clear()
        for c in cameras:
            self._cam_cb.addItem(c["name"], c["id"])

    def _switch_camera(self, idx: int):
        if idx < 0 or idx >= len(self._cameras):
            return
        self._active_cam_id = self._cameras[idx]["id"]

    def _toggle(self):
        if self._analysis_timer.isActive():
            self._analysis_timer.stop()
            self._btn_toggle.setText("Start Monitoring")
            self._btn_toggle.setObjectName("primary_btn")
        else:
            if self._active_cam_id is None and self._cameras:
                self._active_cam_id = self._cameras[0]["id"]
            self._analysis_timer.start(500)  # analyze every 0.5s
            self._btn_toggle.setText("Stop Monitoring")
            self._btn_toggle.setObjectName("danger_btn")

    def receive_frame(self, camera_id: int, frame):
        if camera_id == self._active_cam_id:
            self._current_frame = frame.copy()

    def _analyze(self):
        if self._current_frame is None:
            return
        frame = self._current_frame.copy()
        results = self.ee.analyze(frame)
        annotated = self.ee.draw(frame, results)

        rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        img = QImage(rgb.data.tobytes(), w, h, ch*w, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(img).scaled(
            self._video_lbl.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self._video_lbl.setPixmap(pix)

        for r in results:
            emo = r["dominant_emotion"]
            conf = r["confidence"]
            self._emotion_counter[emo] += 1
            self._add_log(emo, conf)

        # update bars
        total = max(sum(self._emotion_counter.values()), 1)
        for emo, bar in self._bars.items():
            pct = self._emotion_counter.get(emo, 0) / total
            color = "#{:02x}{:02x}{:02x}".format(*EMOTION_COLORS.get(emo, (100,100,100))[::-1])
            bar.setStyleSheet(
                f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                f"stop:0 {color}, stop:{pct:.2f} {color}, "
                f"stop:{min(pct+0.001,1.0):.3f} #252550, stop:1 #252550);"
                f"border-radius:6px;"
            )

    def _add_log(self, emotion: str, confidence: float):
        cam_name = next((c["name"] for c in self._cameras
                         if c["id"] == self._active_cam_id), "Unknown")
        r = self._log_table.rowCount()
        if r > 200: 
            self._log_table.removeRow(0)
            r = self._log_table.rowCount()
        self._log_table.insertRow(r)
        self._log_table.setItem(r, 0, QTableWidgetItem(datetime.now().strftime("%H:%M:%S")))
        self._log_table.setItem(r, 1, QTableWidgetItem(cam_name))
        self._log_table.setItem(r, 2, QTableWidgetItem(emotion.capitalize()))
        self._log_table.setItem(r, 3, QTableWidgetItem(f"{confidence:.1f}%"))
        self._log_table.scrollToBottom()
