import os
from datetime import datetime
from typing import Dict, List

import cv2
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.config import Config
from app.core.recording import Recorder
from app.core.stream_manager import StreamManager
from app.ui.async_task import run_async
from app.ui.widgets.camera_grid import CameraGrid


class LiveView(QWidget):
    active_count_changed = pyqtSignal(int)
    message = pyqtSignal(str)

    def __init__(self, db, stream_manager: StreamManager, recorder: Recorder, parent=None):
        super().__init__(parent)
        self.db = db
        self.sm = stream_manager
        self.recorder = recorder
        self._cameras: List[Dict] = []
        self._connected = set()
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        toolbar = QHBoxLayout()
        title = QLabel("Live View")
        title.setObjectName("page_title")
        toolbar.addWidget(title)
        toolbar.addStretch()
        toolbar.addWidget(QLabel("Layout:"))
        self._layout_cb = QComboBox()
        for r, c in Config.GRID_LAYOUTS:
            self._layout_cb.addItem(f"{r}x{c}", (r, c))
        self._layout_cb.setCurrentIndex(1)
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
        self._grid.set_layout(2, 2)
        self._grid.snapshot_requested.connect(self._take_snapshot)
        root.addWidget(self._grid)

    def load_cameras(self, cameras: List[Dict]):
        old = {c["id"]: c.get("rtsp_url") for c in self._cameras}
        self._cameras = cameras
        new_ids = {c["id"] for c in cameras}
        # Stop streams for removed/disabled cameras and restart changed URLs.
        for cid in list(old):
            if cid not in new_ids and self.sm.is_streaming(cid):
                self.sm.stop_stream(cid)
                self._connected.discard(cid)
        self._grid.set_cameras(cameras)
        for cam in cameras:
            if self.sm.is_streaming(cam["id"]):
                if old.get(cam["id"]) != cam.get("rtsp_url"):
                    self.start_camera(cam["id"])
                else:
                    self._grid.update_status(cam["id"], True, "Connected")
        self._emit_count()

    def _on_layout_change(self, _idx):
        r, c = self._layout_cb.currentData()
        self._grid.set_layout(r, c)
        self._grid.set_cameras(self._cameras)

    def start_camera(self, camera_id: int) -> bool:
        cam = next((c for c in self._cameras if c["id"] == camera_id), None)
        if not cam or not cam.get("rtsp_url"):
            return False
        self.sm.start_stream(cam["id"], cam["rtsp_url"],
                             on_frame=self._grid.update_frame,
                             on_status=self._on_status)
        self._emit_count()
        return True

    def start_all_streams(self):
        for cam in self._cameras:
            if not self.sm.is_streaming(cam["id"]):
                self.start_camera(cam["id"])
        self._emit_count()

    def _on_status(self, cid: int, ok: bool, msg: str):
        self._grid.update_status(cid, ok, msg)
        if ok:
            self._connected.add(cid)
        else:
            self._connected.discard(cid)
        self._emit_count()

    def stop_all_streams(self):
        for cam in self._cameras:
            self.sm.stop_stream(cam["id"])
            self._connected.discard(cam["id"])
            self._grid.update_status(cam["id"], False, "Stopped")
        self._emit_count()

    def _emit_count(self):
        # Only cameras actually delivering video, not ones still retrying.
        self.active_count_changed.emit(
            sum(1 for c in self._cameras
                if c["id"] in self._connected and self.sm.is_streaming(c["id"])))

    def _take_snapshot(self, camera_id: int):
        cam = next((c for c in self._cameras if c["id"] == camera_id), None)
        if not cam or not cam.get("rtsp_url"):
            return

        def grab(url):
            frame = StreamManager.snapshot(url)
            if frame is None:
                return None
            os.makedirs(Config.SNAPSHOTS_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(Config.SNAPSHOTS_DIR, f"snap_{camera_id}_{ts}.jpg")
            cv2.imwrite(path, frame)
            return path

        run_async(grab, lambda path, err: self.message.emit(
            f"Snapshot saved: {path}" if path else "Snapshot failed"),
            cam["rtsp_url"], receiver=self)
