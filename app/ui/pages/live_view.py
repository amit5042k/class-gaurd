from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QComboBox, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from app.ui.widgets.camera_grid import CameraGrid
from app.core.stream_manager import StreamManager
from app.core.recording import Recorder
from app.config import Config
from typing import List, Dict
import cv2, os
from datetime import datetime


class LiveView(QWidget):
    active_count_changed = pyqtSignal(int)

    def __init__(self, db, stream_manager: StreamManager, recorder: Recorder,
                 parent=None):
        super().__init__(parent)
        self.db = db
        self.sm = stream_manager
        self.recorder = recorder
        self._cameras: List[Dict] = []
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Toolbar
        toolbar = QHBoxLayout()
        title = QLabel("Live View")
        title.setObjectName("page_title")
        toolbar.addWidget(title)
        toolbar.addStretch()

        toolbar.addWidget(QLabel("Layout:"))
        self._layout_cb = QComboBox()
        for r, c in Config.GRID_LAYOUTS:
            self._layout_cb.addItem(f"{r}x{c}", (r, c))
        self._layout_cb.setCurrentIndex(1)  # 2x2 default
        self._layout_cb.currentIndexChanged.connect(self._on_layout_change)
        toolbar.addWidget(self._layout_cb)

        self._btn_start_all = QPushButton("Start All")
        self._btn_start_all.setObjectName("success_btn")
        self._btn_start_all.clicked.connect(self.start_all_streams)
        self._btn_stop_all = QPushButton("Stop All")
        self._btn_stop_all.setObjectName("danger_btn")
        self._btn_stop_all.clicked.connect(self.stop_all_streams)
        toolbar.addWidget(self._btn_start_all)
        toolbar.addWidget(self._btn_stop_all)
        root.addLayout(toolbar)

        self._grid = CameraGrid()
        self._grid.snapshot_requested.connect(self._take_snapshot)
        root.addWidget(self._grid)

    def load_cameras(self, cameras: List[Dict]):
        self._cameras = cameras
        self._grid.set_cameras(cameras)

    def _on_layout_change(self, idx):
        r, c = self._layout_cb.currentData()
        self._grid.set_layout(r, c)
        self._grid.set_cameras(self._cameras)

    def start_all_streams(self):
        for cam in self._cameras:
            if not self.sm.is_streaming(cam["id"]) and cam.get("rtsp_url"):
                self.sm.start_stream(
                    cam["id"], cam["rtsp_url"],
                    on_frame=lambda cid, img: self._grid.update_frame(cid, img),
                    on_status=lambda cid, ok, msg: self._on_status(cid, ok, msg),
                )
        self.active_count_changed.emit(
            sum(1 for c in self._cameras if self.sm.is_streaming(c["id"]))
        )

    def stop_all_streams(self):
        for cam in self._cameras:
            self.sm.stop_stream(cam["id"])
        self.active_count_changed.emit(0)

    def _on_status(self, cid: int, ok: bool, msg: str):
        self._grid.update_status(cid, ok, msg)

    def _take_snapshot(self, camera_id: int):
        cam = next((c for c in self._cameras if c["id"] == camera_id), None)
        if not cam or not cam.get("rtsp_url"):
            return
        frame = StreamManager.snapshot(cam["rtsp_url"])
        if frame is not None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(Config.SNAPSHOTS_DIR, f"snap_{camera_id}_{ts}.jpg")
            cv2.imwrite(path, frame)
