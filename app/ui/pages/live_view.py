import os
from datetime import datetime
from typing import Dict, List, Optional

import cv2
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget
)

from app.config import Config
from app.core.stream_manager import StreamManager
from app.ui.async_task import run_async
from app.ui.widgets.camera_grid import CameraGrid
from app.ui.widgets.ptz_panel import PTZPanel

ALL_GROUPS = "All groups"


class LiveView(QWidget):
    active_count_changed = pyqtSignal(int)
    message = pyqtSignal(str)
    fullscreen_requested = pyqtSignal()
    record_requested = pyqtSignal(int, bool)   # camera_id, on

    def __init__(self, db, stream_manager: StreamManager, recorder=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.sm = stream_manager
        self.recorder = recorder
        self._cameras: List[Dict] = []
        self._connected = set()
        self._status: Dict[int, tuple] = {}   # camera_id -> (ok, message), survives re-render
        self._start_queue: List[int] = []
        self._live = False             # Start All pressed: stream what's on screen
        self._pinned: set = set()      # needed elsewhere (recording, AI, monitoring)
        self._recording: Dict[int, bool] = {}
        self._page = 0
        self._single: Optional[int] = None
        self._selected: Optional[int] = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self._toolbar = QWidget()
        toolbar = QHBoxLayout(self._toolbar)
        toolbar.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Live View")
        title.setObjectName("page_title")
        toolbar.addWidget(title)
        toolbar.addStretch()
        self._group_cb = QComboBox()
        self._group_cb.setMinimumWidth(140)
        self._group_cb.currentIndexChanged.connect(self._on_group_change)
        toolbar.addWidget(self._group_cb)
        toolbar.addWidget(QLabel("Layout:"))
        self._layout_cb = QComboBox()
        for r, c in Config.GRID_LAYOUTS:
            self._layout_cb.addItem(f"{r}x{c}", (r, c))
        self._layout_cb.setCurrentIndex(1)
        self._layout_cb.currentIndexChanged.connect(self._on_layout_change)
        toolbar.addWidget(self._layout_cb)
        self._btn_prev = QPushButton("◀")
        self._btn_next = QPushButton("▶")
        self._page_lbl = QLabel()
        self._btn_prev.clicked.connect(lambda: self._go_page(-1))
        self._btn_next.clicked.connect(lambda: self._go_page(1))
        for w in (self._btn_prev, self._page_lbl, self._btn_next):
            toolbar.addWidget(w)
        self._btn_start_all = QPushButton("Start All")
        self._btn_start_all.setObjectName("success_btn")
        self._btn_start_all.clicked.connect(self.start_all_streams)
        self._btn_stop_all = QPushButton("Stop All")
        self._btn_stop_all.setObjectName("danger_btn")
        self._btn_stop_all.clicked.connect(self.stop_all_streams)
        self._btn_full = QPushButton("Full screen")
        self._btn_full.setObjectName("secondary_btn")
        self._btn_full.clicked.connect(self.fullscreen_requested)
        for w in (self._btn_start_all, self._btn_stop_all, self._btn_full):
            toolbar.addWidget(w)
        root.addWidget(self._toolbar)

        body = QHBoxLayout()
        self._grid = CameraGrid()
        self._grid.set_layout(2, 2)
        self._grid.snapshot_requested.connect(self._take_snapshot)
        self._grid.cell_double_clicked.connect(self._toggle_single)
        self._grid.cell_clicked.connect(self._select)
        self._grid.record_toggled.connect(self.record_requested)
        self._grid.fullscreen_requested.connect(lambda _cid: self.fullscreen_requested.emit())
        self._grid.diagnose_requested.connect(self.diagnose)
        body.addWidget(self._grid, 1)
        self.ptz = PTZPanel()
        self.ptz.record_toggled.connect(self.record_requested)
        self.ptz.snapshot_requested.connect(self._take_snapshot)
        body.addWidget(self.ptz)
        root.addLayout(body, 1)

    def set_permissions(self, user):
        self.ptz.set_operator(user.can_operate)
        self._btn_stop_all.setEnabled(user.can_operate)

    def set_chrome_visible(self, visible: bool):
        """Hide toolbar and side panel in full-screen mode."""
        self._toolbar.setVisible(visible)
        self.ptz.setVisible(visible)

    # --------------------------------------------------------------- cameras
    def load_cameras(self, cameras: List[Dict]):
        old = {c["id"]: c.get("rtsp_url") for c in self._cameras}
        self._cameras = cameras
        new_ids = {c["id"] for c in cameras}
        for cid in list(old):
            if cid not in new_ids and self.sm.is_streaming(cid):
                self.sm.stop_stream(cid)
                self._connected.discard(cid)
        groups = sorted({c.get("group_name") or "Default" for c in cameras})
        current = self._group_cb.currentText()
        self._group_cb.blockSignals(True)
        self._group_cb.clear()
        self._group_cb.addItem(ALL_GROUPS)
        self._group_cb.addItems(groups)
        idx = self._group_cb.findText(current)
        self._group_cb.setCurrentIndex(max(0, idx))
        self._group_cb.blockSignals(False)
        if self._single not in new_ids:
            self._single = None
        if self._selected not in new_ids:
            self._select(None)
        else:
            self._select(self._selected)
        self._render()
        for cam in cameras:
            if self.sm.is_streaming(cam["id"]) and old.get(cam["id"]) != cam.get("rtsp_url"):
                self.start_camera(cam["id"])
        self._emit_count()

    def _visible_cameras(self) -> List[Dict]:
        group = self._group_cb.currentText()
        if not group or group == ALL_GROUPS:
            return list(self._cameras)
        return [c for c in self._cameras if (c.get("group_name") or "Default") == group]

    def _render(self):
        cams = self._visible_cameras()
        if self._single is not None:
            single = [c for c in self._cameras if c["id"] == self._single]
            self._grid.set_cameras(single, 1, 1)
            self._page_lbl.setText("Single view")
            self._btn_prev.setEnabled(False)
            self._btn_next.setEnabled(False)
        else:
            size = self._grid.page_size
            pages = max(1, -(-len(cams) // size))
            self._page = min(self._page, pages - 1)
            self._grid.set_cameras(cams[self._page * size:(self._page + 1) * size])
            self._page_lbl.setText(f"Page {self._page + 1}/{pages}")
            self._btn_prev.setEnabled(self._page > 0)
            self._btn_next.setEnabled(self._page < pages - 1)
        for cid in self._grid.visible_ids():
            if cid in self._status and self.sm.is_streaming(cid):
                self._grid.update_status(cid, *self._status[cid])
            elif not self.sm.is_streaming(cid):
                self._grid.update_status(cid, False, "Waiting to connect…" if self._live
                                         else "Not started. Press Start All.")
            self._grid.set_recording(cid, self._recording.get(cid, False),
                                     bool(self.recorder and self.recorder.is_manual(cid)))
        self._grid.select(self._selected)
        self._sync_streams()

    def _sync_streams(self):
        """Like iVMS: only cameras on screen stream, so a 32-channel NVR isn't
        asked for 32 streams at once. Pinned cameras (recording / AI) keep going."""
        if not self._live:
            return
        visible = set(self._grid.visible_ids())
        for cam in self._cameras:
            cid = cam["id"]
            if cid not in visible and cid not in self._pinned and self.sm.is_streaming(cid):
                self.sm.stop_stream(cid)
                self._connected.discard(cid)
                self._status.pop(cid, None)
        self._start_queue = [cid for cid in self._start_queue
                             if cid in visible or cid in self._pinned]
        for cid in self._grid.visible_ids():
            if not self.sm.is_streaming(cid) and cid not in self._start_queue:
                self._start_queue.append(cid)
        self._start_next()
        self._emit_count()

    def set_pinned(self, ids):
        self._pinned = set(ids)

    def _on_layout_change(self, _idx):
        r, c = self._layout_cb.currentData()
        self._grid.set_layout(r, c)
        self._page = 0
        self._single = None
        self._render()

    def _on_group_change(self, _idx):
        self._page = 0
        self._single = None
        self._render()

    def _go_page(self, delta: int):
        self._page = max(0, self._page + delta)
        self._render()

    def _toggle_single(self, camera_id: int):
        self._single = None if self._single == camera_id else camera_id
        self._select(camera_id)
        self._render()

    def _select(self, camera_id: Optional[int]):
        self._selected = camera_id
        cam = next((c for c in self._cameras if c["id"] == camera_id), None)
        self.ptz.set_camera(cam, bool(self.recorder and camera_id is not None
                                      and self.recorder.is_manual(camera_id)))
        self._grid.select(camera_id)

    # ---------------------------------------------------------------- streams
    def start_camera(self, camera_id: int, pin: bool = False) -> bool:
        cam = next((c for c in self._cameras if c["id"] == camera_id), None)
        if not cam or not cam.get("rtsp_url"):
            return False
        if pin:
            self._pinned.add(camera_id)
        self.sm.start_stream(cam["id"], cam["rtsp_url"],
                             on_frame=self._grid.update_frame, on_status=self._on_status,
                             transport=cam.get("rtsp_transport") or "auto")
        self._emit_count()
        return True

    def start_all_streams(self):
        # Stream the cameras on screen, a few at a time: many NVRs reject a
        # burst of simultaneous RTSP requests or limit how many run at once.
        self._live = True
        self._sync_streams()

    def _start_next(self):
        if not self._start_queue:
            return
        cid = self._start_queue.pop(0)
        if not self.sm.is_streaming(cid):
            self.start_camera(cid)
        QTimer.singleShot(400, self._start_next)

    def _on_status(self, cid: int, ok: bool, msg: str):
        self._status[cid] = (ok, msg)
        self._grid.update_status(cid, ok, msg)
        if ok:
            self._connected.add(cid)
        else:
            self._connected.discard(cid)
        self._emit_count()

    def stop_all_streams(self):
        """Stop live viewing. Cameras that record or run AI keep streaming."""
        self._live = False
        self._start_queue.clear()
        for cam in self._cameras:
            if cam["id"] in self._pinned:
                continue
            self._status.pop(cam["id"], None)
            self.sm.stop_stream(cam["id"])
            self._connected.discard(cam["id"])
            self._grid.update_status(cam["id"], False, "Stopped")
        self._emit_count()

    def _emit_count(self):
        self.active_count_changed.emit(
            sum(1 for c in self._cameras
                if c["id"] in self._connected and self.sm.is_streaming(c["id"])))

    def set_recording_state(self, camera_id: int, recording: bool):
        self._recording[camera_id] = recording
        manual = bool(self.recorder and self.recorder.is_manual(camera_id))
        self._grid.set_recording(camera_id, recording, manual)
        if camera_id == self._selected:
            self.ptz.set_manual(manual)

    def connected_ids(self):
        return set(self._connected)

    # --------------------------------------------------------------- diagnose
    def diagnose(self, camera_id: int):
        cam = next((c for c in self._cameras if c["id"] == camera_id), None)
        if cam:
            DiagnoseDialog(cam, self).exec()

    # ---------------------------------------------------------------- snapshot
    def _take_snapshot(self, camera_id: int):
        cam = next((c for c in self._cameras if c["id"] == camera_id), None)
        if not cam or not cam.get("rtsp_url"):
            return

        live = self.sm.latest_frame(camera_id)

        def grab(url):
            # Reuse the running stream: many cameras limit simultaneous connections.
            frame = live if live is not None else StreamManager.snapshot(url)
            if frame is None:
                return None
            os.makedirs(Config.SNAPSHOTS_DIR, exist_ok=True)
            path = os.path.join(Config.SNAPSHOTS_DIR,
                                f"snap_{camera_id}_{datetime.now():%Y%m%d_%H%M%S}.jpg")
            cv2.imwrite(path, frame)
            return path

        run_async(grab, lambda path, err: self.message.emit(
            f"Snapshot saved: {path}" if path else "Snapshot failed"),
            cam["rtsp_url"], receiver=self)


class DiagnoseDialog(QDialog):
    """Runs the connection test in the background and shows a copyable report."""

    def __init__(self, cam: Dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Diagnose: {cam['name']}")
        self.resize(760, 520)
        lay = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setStyleSheet("font-family: Consolas, 'DejaVu Sans Mono', monospace;")
        self.text.setPlainText("Testing the connection (network, login, then video over TCP "
                               "and UDP). This takes up to 30 seconds…")
        lay.addWidget(self.text)
        row = QHBoxLayout()
        self.copy_btn = QPushButton("Copy report")
        self.copy_btn.setObjectName("primary_btn")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addStretch()
        row.addWidget(self.copy_btn)
        row.addWidget(close)
        lay.addLayout(row)
        from app.core.diagnose import run_diagnosis
        run_async(run_diagnosis, self._done, cam, receiver=self)

    def _done(self, report, error):
        self.text.setPlainText(report if report else f"Diagnosis failed: {error}")
        self.copy_btn.setEnabled(bool(report))

    def _copy(self):
        QApplication.clipboard().setText(self.text.toPlainText())
        self.copy_btn.setText("Copied")
