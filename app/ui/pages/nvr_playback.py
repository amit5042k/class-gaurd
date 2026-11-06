"""Recordings tab "On the NVR": search and play what the recorder itself stored."""
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import cv2
from PyQt6.QtCore import QDate, QThread, QTime, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QComboBox, QDateEdit, QHBoxLayout, QLabel, QPushButton, QTimeEdit, QVBoxLayout, QWidget
)

from app.config import Config
from app.protocols.nvr_playback import (
    can_search, playback_url, search_recordings, supports_playback
)
from app.ui.async_task import run_async
from app.ui.widgets.video_player import KIND_COLORS, DayTimeline
from app.ui.widgets.zone_canvas import VideoCanvas

logger = logging.getLogger(__name__)

FIRST_PICTURE_TIMEOUT_S = 15
DISPLAY_FPS = 15


class PlaybackThread(QThread):
    """Plays one RTSP playback address; tries the direct decoder, then the
    standard one. Position advances with the device's real-time pace."""

    frame_ready = pyqtSignal(QImage)
    position = pyqtSignal(object)          # datetime
    status = pyqtSignal(str)
    finished_playing = pyqtSignal(str)     # reason

    def __init__(self, url: str, start: datetime, parent=None):
        super().__init__(parent)
        self.url = url
        self.start_time = start
        self._running = True
        self.last_frame = None
        self._clip_path: Optional[str] = None
        self._writer = None
        self._lock_clip = False

    def stop(self):
        self._running = False

    def start_clip(self, path: str):
        self._clip_path = path

    def stop_clip(self) -> Optional[str]:
        path, self._clip_path = self._clip_path, None
        return path

    def _backends(self) -> List[str]:
        from app.core.av_capture import AVAILABLE
        return (["rtp"] if AVAILABLE else []) + ["opencv"]

    def run(self):
        from app.core.stream_manager import is_placeholder_frame, open_capture, to_qimage
        reason = "The recording ended."
        for backend in self._backends():
            if not self._running:
                return
            self.status.emit("Connecting to the recorder…")
            cap = open_capture(self.url, "tcp", backend)
            try:
                if not cap.isOpened():
                    reason = "The recorder refused the playback request (no recording at " \
                             "that time, or it allows no more playback sessions)."
                    continue
                t_open, t_first, shown = time.monotonic(), None, 0.0
                frames = 0
                while self._running:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    if t_first is None:
                        if is_placeholder_frame(frame) and \
                                time.monotonic() - t_open < FIRST_PICTURE_TIMEOUT_S:
                            continue          # wait for the first full picture
                        t_first = time.monotonic()
                        self.status.emit("Playing")
                    frames += 1
                    self.last_frame = frame
                    self._write_clip(frame, frames, t_first)
                    now = time.monotonic()
                    if now - shown >= 1 / DISPLAY_FPS:
                        shown = now
                        self.frame_ready.emit(to_qimage(frame, 1280))
                        self.position.emit(self.start_time + timedelta(seconds=now - t_first))
                if t_first is not None:
                    reason = "The recording ended." if self._running else ""
                    break
                reason = "No picture from the recorder for that time."
            finally:
                cap.release()
                self._close_clip()
        if self._running:
            self.finished_playing.emit(reason)

    def _write_clip(self, frame, frames: int, t_first: float):
        if not self._clip_path:
            self._close_clip()
            return
        if self._writer is None:
            elapsed = max(0.5, time.monotonic() - t_first)
            fps = min(30.0, max(5.0, frames / elapsed)) if frames > 10 else 20.0
            h, w = frame.shape[:2]
            os.makedirs(os.path.dirname(self._clip_path), exist_ok=True)
            self._writer = cv2.VideoWriter(self._clip_path, cv2.VideoWriter_fourcc(*"mp4v"),
                                           fps, (w, h))
        self._writer.write(frame)

    def _close_clip(self):
        if self._writer is not None:
            self._writer.release()
            self._writer = None


class NVRPlaybackTab(QWidget):
    message = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cameras: List[Dict] = []
        self._thread: Optional[PlaybackThread] = None
        self._retired: List[PlaybackThread] = []
        self._pos: Optional[datetime] = None
        self._segments: List[Dict] = []
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(8)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Camera:"))
        self.cam_cb = QComboBox()
        self.cam_cb.setMinimumWidth(200)
        self.cam_cb.currentIndexChanged.connect(lambda _i: self.search())
        hdr.addWidget(self.cam_cb)
        hdr.addWidget(QLabel("Date:"))
        self.date = QDateEdit(calendarPopup=True)
        self.date.setDisplayFormat("dd MMM yyyy")
        self.date.setDate(QDate.currentDate())
        self.date.dateChanged.connect(lambda _d: self.search())
        hdr.addWidget(self.date)
        hdr.addWidget(QLabel("From:"))
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm:ss")
        self.time.setTime(QTime(8, 0))
        hdr.addWidget(self.time)
        self.btn_play = QPushButton("Play")
        self.btn_play.setObjectName("primary_btn")
        self.btn_play.clicked.connect(self._play_clicked)
        hdr.addWidget(self.btn_play)
        btn_search = QPushButton("Search")
        btn_search.setObjectName("secondary_btn")
        btn_search.clicked.connect(self.search)
        hdr.addWidget(btn_search)
        hdr.addStretch()
        root.addLayout(hdr)

        self.timeline = DayTimeline()
        self.timeline.time_clicked.connect(self.play_at)
        root.addWidget(self.timeline)
        legend = QHBoxLayout()
        for k in ("continuous", "motion", "event"):
            lbl = QLabel(f"■ {k.capitalize()}")
            lbl.setStyleSheet(f"color:{KIND_COLORS[k]}; font-size:11px;")
            legend.addWidget(lbl)
        legend.addStretch()
        self.found = QLabel()
        self.found.setStyleSheet("color:#999; font-size:11px;")
        legend.addWidget(self.found)
        root.addLayout(legend)

        self.video = VideoCanvas("Pick a camera and a time, then press Play "
                                 "(or click the timeline)")
        root.addWidget(self.video, 1)

        ctl = QHBoxLayout()
        self.btn_back = QPushButton("⏪ 30 s")
        self.btn_back.clicked.connect(lambda: self._jump(-30))
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_fwd = QPushButton("30 s ⏩")
        self.btn_fwd.clicked.connect(lambda: self._jump(30))
        self.btn_snap = QPushButton("Snapshot")
        self.btn_snap.clicked.connect(self._snapshot)
        self.btn_clip = QPushButton("Save clip")
        self.btn_clip.setCheckable(True)
        self.btn_clip.setToolTip("Save what plays from now on as a video file on this PC")
        self.btn_clip.toggled.connect(self._toggle_clip)
        for b in (self.btn_back, self.btn_pause, self.btn_fwd, self.btn_snap, self.btn_clip):
            ctl.addWidget(b)
        ctl.addStretch()
        self.pos_lbl = QLabel("—")
        self.pos_lbl.setStyleSheet("font-size:14px; font-weight:bold;")
        ctl.addWidget(self.pos_lbl)
        self.status_lbl = QLabel()
        self.status_lbl.setStyleSheet("color:#999;")
        ctl.addWidget(self.status_lbl)
        root.addLayout(ctl)
        self._set_controls(False)

    # ---------------------------------------------------------------- data
    def set_cameras(self, cameras: List[Dict]):
        current = self.cam_cb.currentData()
        self._cameras = [c for c in cameras if supports_playback(c)]
        self.cam_cb.blockSignals(True)
        self.cam_cb.clear()
        for c in self._cameras:
            self.cam_cb.addItem(c["name"], c["id"])
        idx = self.cam_cb.findData(current)
        self.cam_cb.setCurrentIndex(max(idx, 0))
        self.cam_cb.blockSignals(False)
        if not self._cameras:
            self.video.clear_image("No camera supports recorder playback. It works for "
                                   "Hikvision, Dahua, CP Plus and UNV recorders and cameras "
                                   "added with their brand (not a custom RTSP address).")

    def camera(self) -> Optional[Dict]:
        cid = self.cam_cb.currentData()
        return next((c for c in self._cameras if c["id"] == cid), None)

    def has_camera(self, camera_id: int) -> bool:
        return any(c["id"] == camera_id for c in self._cameras)

    def search(self):
        cam = self.camera()
        day = self.date.date().toPyDate()
        self._segments = []
        self.timeline.set_data(day, [])
        if cam is None:
            return
        if not can_search(cam):
            self.found.setText(f"{cam['brand']} recorders can't be searched: pick a time "
                               "and press Play.")
            return
        self.found.setText("Searching the recorder…")
        cid = cam["id"]

        def done(segments, error):
            if self.cam_cb.currentData() != cid or self.date.date().toPyDate() != day:
                return
            if error:
                self.found.setText(f"Search failed: {error}")
                return
            self._segments = segments
            self.timeline.set_data(day, segments)
            if segments:
                total = sum((s["end"] - s["start"]).total_seconds() for s in segments)
                self.found.setText(f"{len(segments)} recorded periods, "
                                   f"{total / 3600:.1f} h in total")
                if day != datetime.now().date():
                    first = segments[0]["start"]
                    self.time.setTime(QTime(first.hour, first.minute, first.second))
            else:
                self.found.setText("The recorder has nothing recorded for this camera on "
                                   "this day.")
        run_async(search_recordings, done, cam, day, receiver=self)

    # ------------------------------------------------------------- playback
    def _play_clicked(self):
        t = self.time.time()
        d = self.date.date()
        self.play_at(datetime(d.year(), d.month(), d.day(), t.hour(), t.minute(), t.second()))

    def open_camera_at(self, camera_id: int, when: datetime):
        """From Live View / an alert: play that camera's recording at `when`."""
        idx = self.cam_cb.findData(camera_id)
        if idx < 0:
            return False
        self.cam_cb.blockSignals(True)
        self.cam_cb.setCurrentIndex(idx)
        self.cam_cb.blockSignals(False)
        self.date.blockSignals(True)
        self.date.setDate(QDate(when.year, when.month, when.day))
        self.date.blockSignals(False)
        self.search()
        self.play_at(when)
        return True

    def play_at(self, when: datetime):
        cam = self.camera()
        if cam is None:
            return
        if when > datetime.now():
            when = datetime.now() - timedelta(seconds=10)
        self.time.setTime(QTime(when.hour, when.minute, when.second))
        url = playback_url(cam, when)
        if not url:
            return
        self.stop()
        self._pos = when
        self.timeline.set_marker(when)
        self.pos_lbl.setText(when.strftime("%d %b %H:%M:%S"))
        self.video.clear_image("Connecting to the recorder…")
        t = PlaybackThread(url, when)
        t.frame_ready.connect(self.video.set_image)
        t.position.connect(self._on_position)
        t.status.connect(self.status_lbl.setText)
        t.finished_playing.connect(self._on_finished)
        self._thread = t
        t.start()
        self.btn_pause.setText("Pause")
        self._set_controls(True)

    def _on_position(self, when: datetime):
        self._pos = when
        self.pos_lbl.setText(when.strftime("%d %b %H:%M:%S"))
        self.timeline.set_marker(when)

    def _on_finished(self, reason: str):
        self.status_lbl.setText(reason)
        if not self.video.has_image():
            self.video.clear_image(reason)
        if self.btn_clip.isChecked():
            self.btn_clip.setChecked(False)
        self.btn_pause.setText("Play")

    def stop(self):
        t, self._thread = self._thread, None
        if t is not None:
            for sig in (t.frame_ready, t.position, t.status, t.finished_playing):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            t.stop()
            self._retired.append(t)
            t.finished.connect(lambda t=t: t in self._retired and self._retired.remove(t))

    def _toggle_pause(self):
        if self._thread is not None and self._thread.isRunning():
            if self.btn_clip.isChecked():
                self.btn_clip.setChecked(False)
            self.stop()
            self.btn_pause.setText("Play")
            self.status_lbl.setText("Paused")
        elif self._pos is not None:
            self.play_at(self._pos)

    def _jump(self, seconds: int):
        if self._pos is not None:
            self.play_at(self._pos + timedelta(seconds=seconds))

    def _snapshot(self):
        frame = self._thread.last_frame if self._thread else None
        if frame is None:
            return
        os.makedirs(Config.SNAPSHOTS_DIR, exist_ok=True)
        when = self._pos or datetime.now()
        path = os.path.join(Config.SNAPSHOTS_DIR, f"nvr_{self.cam_cb.currentData()}_"
                                                  f"{when:%Y%m%d_%H%M%S}.jpg")
        cv2.imwrite(path, frame)
        self.message.emit(f"Snapshot saved: {path}")

    def _toggle_clip(self, on: bool):
        t = self._thread
        if on:
            if t is None or not t.isRunning():
                self.btn_clip.setChecked(False)
                return
            when = self._pos or datetime.now()
            path = os.path.join(Config.RECORDINGS_DIR, "exports",
                                f"nvr_{self.cam_cb.currentData()}_{when:%Y%m%d_%H%M%S}.mp4")
            t.start_clip(path)
            self.btn_clip.setText("Stop clip")
            self.message.emit("Saving clip… press Stop clip to finish.")
        else:
            self.btn_clip.setText("Save clip")
            path = t.stop_clip() if t is not None else None
            if path:
                self.message.emit(f"Clip saved: {path}")

    def _set_controls(self, on: bool):
        for b in (self.btn_back, self.btn_pause, self.btn_fwd, self.btn_snap, self.btn_clip):
            b.setEnabled(on)

    def shutdown(self):
        self.stop()
        for t in list(self._retired):
            t.wait(3000)
