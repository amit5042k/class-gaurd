import os
from datetime import datetime, timedelta
from typing import List, Optional

import cv2
from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget
)

from app.core.stream_manager import to_qimage
from app.ui.widgets.zone_canvas import VideoCanvas

KIND_COLORS = {"continuous": "#2f6fd6", "motion": "#f39c12", "manual": "#27ae60",
               "event": "#e74c3c"}


def fmt_secs(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 \
        else f"{s // 60:02d}:{s % 60:02d}"


class VideoPlayer(QWidget):
    """Plays recorded files with OpenCV (same codecs the recorder writes)."""

    snapshot_saved = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cap: Optional[cv2.VideoCapture] = None
        self._path = ""
        self._fps = 15.0
        self._frames = 0
        self._pos = 0
        self._start_time: Optional[datetime] = None
        self._last_frame = None
        self._skip = 1
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._build_ui()
        self._set_enabled(False)

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._canvas = VideoCanvas("Choose a recording to play")
        lay.addWidget(self._canvas, 1)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.sliderMoved.connect(self._seek)
        self._slider.sliderPressed.connect(self._timer.stop)
        self._slider.sliderReleased.connect(self._resume_after_drag)
        lay.addWidget(self._slider)
        row = QHBoxLayout()
        self._btn_back = QPushButton("⏪ 10s")
        self._btn_play = QPushButton("▶ Play")
        self._btn_play.setObjectName("primary_btn")
        self._btn_fwd = QPushButton("10s ⏩")
        self._btn_step = QPushButton("Next frame")
        self._speed = QComboBox()
        for label, val in (("0.5×", 0.5), ("1×", 1.0), ("2×", 2.0), ("4×", 4.0), ("8×", 8.0)):
            self._speed.addItem(label, val)
        self._speed.setCurrentIndex(1)
        self._speed.currentIndexChanged.connect(self._apply_speed)
        self._btn_snap = QPushButton("Snapshot")
        self._btn_snap.setObjectName("secondary_btn")
        self._time = QLabel("--:-- / --:--")
        self._time.setMinimumWidth(250)
        self._btn_back.clicked.connect(lambda: self._jump(-10))
        self._btn_fwd.clicked.connect(lambda: self._jump(10))
        self._btn_play.clicked.connect(self.toggle)
        self._btn_step.clicked.connect(self._step)
        self._btn_snap.clicked.connect(self._snapshot)
        for w in (self._btn_back, self._btn_play, self._btn_fwd, self._btn_step):
            row.addWidget(w)
        row.addWidget(QLabel("Speed:"))
        row.addWidget(self._speed)
        row.addWidget(self._time, 1)
        row.addWidget(self._btn_snap)
        lay.addLayout(row)

    def _set_enabled(self, on: bool):
        for w in (self._slider, self._btn_back, self._btn_play, self._btn_fwd,
                  self._btn_step, self._speed, self._btn_snap):
            w.setEnabled(on)

    # ------------------------------------------------------------- control
    def open(self, path: str, start_time: Optional[datetime] = None,
             offset_s: float = 0.0, autoplay: bool = True) -> bool:
        self.close_file()
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            self._canvas.clear_image("This file can't be played (still recording, or damaged).")
            return False
        self._cap, self._path, self._start_time = cap, path, start_time
        self._fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
        self._frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self._slider.setRange(0, max(0, self._frames - 1))
        self._set_enabled(True)
        self._seek(int(max(0.0, offset_s) * self._fps))
        if autoplay:
            self.play()
        return True

    def close_file(self):
        self.pause()
        if self._cap is not None:
            self._cap.release()
        self._cap, self._path, self._last_frame = None, "", None
        self._set_enabled(False)
        self._canvas.clear_image("Choose a recording to play")
        self._time.setText("--:-- / --:--")

    @property
    def path(self) -> str:
        return self._path

    def is_playing(self) -> bool:
        return self._timer.isActive()

    def play(self):
        if self._cap is None:
            return
        if self._pos >= self._frames - 1:
            self._seek(0)
        self._apply_speed()
        self._timer.start()
        self._btn_play.setText("⏸ Pause")

    def pause(self):
        self._timer.stop()
        self._btn_play.setText("▶ Play")

    def toggle(self):
        self.pause() if self.is_playing() else self.play()

    def _apply_speed(self):
        speed = self._speed.currentData() or 1.0
        # Above ~30 redraws/s skip frames instead of shortening the interval.
        self._skip = max(1, int(round(self._fps * speed / 30)))
        self._timer.setInterval(max(5, int(1000 * self._skip / (self._fps * speed))))

    def _resume_after_drag(self):
        if self._btn_play.text().startswith("⏸"):
            self._timer.start()

    def _seek(self, frame_no: int):
        if self._cap is None:
            return
        frame_no = max(0, min(frame_no, max(0, self._frames - 1)))
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        self._pos = frame_no
        self._read_and_show()

    def _jump(self, seconds: float):
        self._seek(self._pos + int(seconds * self._fps))

    def _step(self):
        self.pause()
        self._read_and_show()

    def _tick(self):
        for _ in range(getattr(self, "_skip", 1) - 1):
            if not self._cap.grab():
                break
            self._pos += 1
        if not self._read_and_show():
            self.pause()

    def _read_and_show(self) -> bool:
        if self._cap is None:
            return False
        ok, frame = self._cap.read()
        if not ok:
            return False
        self._last_frame = frame
        self._pos = int(self._cap.get(cv2.CAP_PROP_POS_FRAMES))
        self._canvas.set_image(to_qimage(frame))
        if not self._slider.isSliderDown():
            self._slider.setValue(self._pos)
        cur, total = self._pos / self._fps, self._frames / self._fps
        clock = ""
        if self._start_time:
            clock = (self._start_time + timedelta(seconds=cur)).strftime("  (%H:%M:%S)")
        self._time.setText(f"{fmt_secs(cur)} / {fmt_secs(total)}{clock}")
        return True

    def _snapshot(self):
        if self._last_frame is None:
            return
        from app.config import Config
        os.makedirs(Config.SNAPSHOTS_DIR, exist_ok=True)
        path = os.path.join(Config.SNAPSHOTS_DIR,
                            f"playback_{datetime.now():%Y%m%d_%H%M%S_%f}.jpg")
        cv2.imwrite(path, self._last_frame)
        self.snapshot_saved.emit(path)


class DayTimeline(QWidget):
    """24-hour bar showing recorded periods; click to jump to that time."""

    time_clicked = pyqtSignal(object)  # datetime

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._day: Optional[datetime] = None
        self._segments: List[dict] = []
        self._marker: Optional[datetime] = None

    def set_data(self, day, segments: List[dict]):
        self._day = datetime.combine(day, datetime.min.time())
        self._segments = segments
        self.update()

    def set_marker(self, when: Optional[datetime]):
        self._marker = when
        self.update()

    def _x(self, when: datetime) -> float:
        secs = (when - self._day).total_seconds()
        return 8 + (self.width() - 16) * max(0.0, min(1.0, secs / 86400))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(8, 6, self.width() - 16, 18)
        p.fillRect(bar, QColor("#1b1b30"))
        if self._day is not None:
            end_of_day = self._day + timedelta(days=1)
            for seg in self._segments:
                end = seg["end"] or datetime.now()
                x1, x2 = self._x(max(seg["start"], self._day)), self._x(min(end, end_of_day))
                p.fillRect(QRectF(x1, bar.y(), max(1.5, x2 - x1), bar.height()),
                           QColor(KIND_COLORS.get(seg["kind"], "#888")))
            p.setPen(QColor("#666"))
            for h in range(0, 25, 3):
                x = 8 + (self.width() - 16) * h / 24
                p.drawLine(int(x), 24, int(x), 29)
                if h < 24:
                    p.drawText(max(0, int(x) - 14), 42, f"{h:02d}:00")
            if self._marker and self._day <= self._marker < end_of_day:
                p.setPen(QPen(QColor("#ffd84a"), 2))
                x = self._x(self._marker)
                p.drawLine(int(x), 2, int(x), 28)
        p.setPen(QColor("#353580"))
        p.drawRect(bar)
        p.end()

    def mousePressEvent(self, event):
        if self._day is None:
            return
        frac = (event.position().x() - 8) / max(1, self.width() - 16)
        self.time_clicked.emit(self._day + timedelta(seconds=86400 * max(0.0, min(1.0, frac))))
