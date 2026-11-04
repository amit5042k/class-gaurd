from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout,
    QWidget
)

from app.protocols.ptz import controller_for


class PTZPanel(QWidget):
    """Side panel acting on the selected camera: PTZ, recording, snapshot."""

    record_toggled = pyqtSignal(int, bool)
    snapshot_requested = pyqtSignal(int)
    _result = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(230)
        self._cam: Optional[Dict] = None
        self._ctrl = None
        self._pool = ThreadPoolExecutor(max_workers=1)  # keeps move/stop in order
        self._manual = False
        self._operator = True
        self._result.connect(self._show_result)
        self._build_ui()
        self.set_camera(None)

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 0, 0, 0)
        self._title = QLabel()
        self._title.setWordWrap(True)
        self._title.setStyleSheet("font-weight:bold;")
        lay.addWidget(self._title)

        box = QGroupBox("Pan / tilt / zoom")
        g = QGridLayout(box)
        self._ptz_buttons = []
        for text, pos, direction in (("▲", (0, 1), "up"), ("◀", (1, 0), "left"),
                                     ("▶", (1, 2), "right"), ("▼", (2, 1), "down")):
            b = self._hold_button(text, direction)
            g.addWidget(b, *pos)
        zin = self._hold_button("Zoom +", "zoom_in")
        zout = self._hold_button("Zoom −", "zoom_out")
        g.addWidget(zin, 3, 0, 1, 2)
        g.addWidget(zout, 3, 2, 1, 1)
        g.addWidget(QLabel("Speed"), 4, 0)
        self._speed = QSlider(Qt.Orientation.Horizontal)
        self._speed.setRange(10, 100)
        self._speed.setValue(50)
        g.addWidget(self._speed, 4, 1, 1, 2)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Preset"))
        self._preset = QSpinBox()
        self._preset.setRange(1, 255)
        prow.addWidget(self._preset)
        self._btn_preset = QPushButton("Go")
        self._btn_preset.setObjectName("secondary_btn")
        self._btn_preset.clicked.connect(self._goto_preset)
        prow.addWidget(self._btn_preset)
        g.addLayout(prow, 5, 0, 1, 3)
        lay.addWidget(box)

        self._btn_rec = QPushButton("● Start recording")
        self._btn_rec.setObjectName("danger_btn")
        self._btn_rec.clicked.connect(self._toggle_record)
        self._btn_snap = QPushButton("Snapshot")
        self._btn_snap.setObjectName("secondary_btn")
        self._btn_snap.clicked.connect(
            lambda: self._cam and self.snapshot_requested.emit(self._cam["id"]))
        lay.addWidget(self._btn_rec)
        lay.addWidget(self._btn_snap)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#9098b1; font-size:11px;")
        lay.addWidget(self._status)
        lay.addStretch()

    def _hold_button(self, text: str, direction: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("secondary_btn")
        b.setAutoRepeat(False)
        b.pressed.connect(lambda d=direction: self._send("move", d))
        b.released.connect(lambda: self._send("stop"))
        self._ptz_buttons.append(b)
        return b

    # ---------------------------------------------------------------- state
    def set_camera(self, cam: Optional[Dict], manual_recording: bool = False):
        self._cam = cam
        self._ctrl = None
        self._status.setText("")
        enabled = cam is not None
        if cam:
            self._title.setText(cam["name"])
        else:
            self._title.setText("Click a camera tile to control it")
        ptz_ok = enabled and cam.get("camera_type") == "PTZ Camera" and self._operator
        for w in self._ptz_buttons + [self._speed, self._preset, self._btn_preset]:
            w.setEnabled(ptz_ok)
        if enabled and not self._operator:
            self._status.setText("Your account can watch cameras but not move or record "
                                 "them.")
        elif enabled and not ptz_ok:
            self._status.setText("PTZ controls are enabled for cameras whose type is "
                                 "'PTZ Camera' (Camera Management → Edit).")
        self._btn_rec.setEnabled(enabled and self._operator)
        self._btn_snap.setEnabled(enabled)
        self.set_manual(manual_recording)

    def set_operator(self, on: bool):
        self._operator = on
        self.set_camera(self._cam, self._manual)

    def set_manual(self, on: bool):
        self._manual = on
        self._btn_rec.setText("■ Stop recording" if on else "● Start recording")

    def _toggle_record(self):
        if self._cam:
            self.record_toggled.emit(self._cam["id"], not self._manual)

    # ------------------------------------------------------------------ PTZ
    def _controller(self):
        if self._ctrl is None and self._cam:
            self._ctrl = controller_for(self._cam)
        return self._ctrl

    def _send(self, what: str, arg=None):
        if not self._cam:
            return
        ctrl, ch, speed = self._controller(), int(self._cam.get("channel") or 1), \
            self._speed.value()

        def job():
            try:
                if what == "move":
                    ok = ctrl.ptz_move(ch, arg, speed)
                elif what == "stop":
                    ok = ctrl.ptz_stop(ch)
                else:
                    ok = ctrl.ptz_goto_preset(ch, arg)
                if not ok:
                    self._result.emit("The camera refused the PTZ command.")
                elif what == "preset":
                    self._result.emit(f"Moving to preset {arg}.")
            except Exception as e:
                self._result.emit(f"PTZ failed: {e}")

        self._pool.submit(job)

    def _goto_preset(self):
        self._send("preset", self._preset.value())

    def _show_result(self, msg: str):
        self._status.setText(msg)

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
