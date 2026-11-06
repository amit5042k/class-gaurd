from PyQt6.QtCore import QByteArray, QMimeData, QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QDrag, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton, QVBoxLayout, QWidget
)

# Drag-and-drop payload: "<camera_id>:<source window or -1>"
CAMERA_MIME = "application/x-classguard-camera"
MAX_ZOOM = 8.0


def camera_mime(camera_id: int, from_slot: int = -1) -> QMimeData:
    mime = QMimeData()
    mime.setData(CAMERA_MIME, QByteArray(f"{camera_id}:{from_slot}".encode()))
    return mime


def parse_camera_mime(mime: QMimeData):
    """(camera_id, from_slot) or None."""
    if not mime.hasFormat(CAMERA_MIME):
        return None
    try:
        cid, slot = bytes(mime.data(CAMERA_MIME)).decode().split(":")
        return int(cid), int(slot)
    except ValueError:
        return None


class VideoCell(QWidget):
    """One playback window. Shows a camera (or "drag a camera here"), a hover
    toolbar, digital zoom, and accepts cameras dropped from the device tree or
    from another window."""

    double_clicked = pyqtSignal(int)   # camera_id
    clicked = pyqtSignal(int)
    snapshot_requested = pyqtSignal(int)
    fullscreen_requested = pyqtSignal(int)
    record_toggled = pyqtSignal(int, bool)   # camera_id, start?
    diagnose_requested = pyqtSignal(int)
    close_requested = pyqtSignal(int)
    playback_requested = pyqtSignal(int)
    camera_dropped = pyqtSignal(int, int, int)   # window, camera_id, from window (-1 = tree)
    window_clicked = pyqtSignal(int)             # window index (also for empty windows)

    def __init__(self, camera_id: int, camera_name: str, parent=None, slot: int = -1):
        super().__init__(parent)
        self.camera_id = camera_id
        self.camera_name = camera_name
        self.slot = slot
        self._connected = False
        self._recording = False
        self._manual = False
        self._zoom_mode = False
        self._zoom = 1.0
        self._center = (0.5, 0.5)
        self._last_img = None
        self._press_pos = None
        self.setObjectName("video_cell")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top = QWidget()
        top.setObjectName("cam_name_bar")
        top.setFixedHeight(24)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(6, 0, 6, 0)
        self._status_dot = QLabel("●")
        self._status_dot.setObjectName("disconnected_dot")
        self._name_lbl = QLabel(self.camera_name)
        self._name_lbl.setObjectName("cam_name_bar")
        self._zoom_lbl = QLabel()
        self._zoom_lbl.setStyleSheet("color:#ffd84a; font-size:10px;")
        self._rec_lbl = QLabel("● REC")
        self._rec_lbl.setObjectName("recording_dot")
        self._rec_lbl.setStyleSheet("color:#e74c3c; font-size:10px;")
        self._rec_lbl.setVisible(False)
        self._status_dot.setVisible(bool(self.camera_name))
        tl.addWidget(self._status_dot)
        tl.addWidget(self._name_lbl)
        tl.addStretch()
        self._mode_lbl = QLabel()
        self._mode_lbl.setVisible(False)
        tl.addWidget(self._mode_lbl)
        tl.addWidget(self._zoom_lbl)
        tl.addWidget(self._rec_lbl)
        root.addWidget(top)

        self._canvas = QLabel()
        self._canvas.setObjectName("video_canvas")
        self._canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Ignored: otherwise each scaled frame enlarges the label's size hint
        # and the tiles keep growing.
        self._canvas.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._canvas.setMinimumSize(1, 1)
        self._canvas.setWordWrap(True)
        self._canvas.setMouseTracking(True)
        self._canvas.setText("No Signal" if self.camera_name else
                             "Drag a camera here\nor double-click one in the device list")
        self._canvas.setStyleSheet("color:#444; font-size:13px;")
        root.addWidget(self._canvas)

        # Hover toolbar, like the window toolbar of desktop VMS clients.
        self._tools = QWidget()
        self._tools.setObjectName("cell_toolbar")
        self._tools.setStyleSheet("#cell_toolbar{background:rgba(10,10,25,200);}"
                                  "QToolButton{color:#dde1ec;border:none;padding:2px 6px;"
                                  "font-size:11px;}QToolButton:hover{color:#ef4565;}"
                                  "QToolButton:checked{color:#ffd84a;}")
        bl = QHBoxLayout(self._tools)
        bl.setContentsMargins(4, 0, 4, 0)
        bl.setSpacing(2)

        def tool(text, tip, slot, checkable=False):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.clicked.connect(slot)
            bl.addWidget(b)
            return b

        self._b_snap = tool("Snapshot", "Save a picture", lambda: self.snapshot_requested.emit(
            self.camera_id))
        self._b_rec = tool("Record", "Start / stop manual recording", self._toggle_rec, True)
        self._b_zoom = tool("Zoom", "Digital zoom: scroll the mouse wheel over the picture",
                            self._toggle_zoom, True)
        self._b_replay = tool("Replay", "Instant playback of the last 30 seconds",
                              lambda: self.playback_requested.emit(self.camera_id))
        bl.addStretch()
        self._b_close = tool("Close", "Close this window (stops its live view)",
                             lambda: self.close_requested.emit(self.camera_id))
        self._tools.setFixedHeight(24)
        self._tools.setVisible(False)
        root.addWidget(self._tools)

    # ----------------------------------------------------------------- slots
    def update_frame(self, camera_id: int, img: QImage):
        if camera_id != self.camera_id:
            return
        if not self._connected:
            self.set_status(True)
        self._last_img = img
        self._paint()

    def _paint(self):
        img = self._last_img
        if img is None:
            return
        if self._zoom > 1.0:
            w, h = int(img.width() / self._zoom), int(img.height() / self._zoom)
            cx, cy = self._center
            x = min(max(0, int(cx * img.width() - w / 2)), img.width() - w)
            y = min(max(0, int(cy * img.height() - h / 2)), img.height() - h)
            img = img.copy(QRect(x, y, w, h))
        self._canvas.setPixmap(QPixmap.fromImage(img).scaled(
            self._canvas.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation if self._zoom <= 1.0
            else Qt.TransformationMode.SmoothTransformation))

    def set_status(self, connected: bool, msg: str = ""):
        self._connected = connected
        if connected:
            self._status_dot.setObjectName("connected_dot")
            self._status_dot.setStyleSheet("color:#27ae60; font-size:14px;")
            self._canvas.setStyleSheet("")
            # Show when the picture is not normal live video.
            if msg.startswith("Still pictures"):
                self._badge("STILL", "#7fb3ff", msg)
            elif msg and not msg.startswith("Connected"):
                self._badge("⚠ NO FULL PICTURE", "#f39c12", msg)
            else:
                self._badge("", "", msg)
        elif msg.startswith("Connect") and self._last_img is not None:
            # Still connecting: keep the picture already shown (e.g. a still).
            self._status_dot.setStyleSheet("color:#f39c12; font-size:14px;")
            self._badge("CONNECTING", "#f39c12", msg)
        else:
            self._last_img = None
            self._badge("", "", "")
            self._status_dot.setObjectName("disconnected_dot")
            self._canvas.setPixmap(QPixmap())  # clears text too, so do it first
            connecting = msg.startswith("Connecting")
            self._canvas.setText(msg if connecting else f"Disconnected\n{msg}")
            self._status_dot.setStyleSheet(
                f"color:{'#f39c12' if connecting else '#ef4565'}; font-size:14px;")
            self._canvas.setStyleSheet("color:#555; font-size:12px;")

    def _badge(self, text: str, color: str, tip: str):
        self._mode_lbl.setText(text)
        self._mode_lbl.setStyleSheet(f"color:{color}; font-size:10px; font-weight:bold;")
        self._mode_lbl.setVisible(bool(text))
        self.setToolTip(tip if text else "")

    def set_recording(self, recording: bool, manual: bool = False):
        self._recording = recording
        self._manual = manual
        self._rec_lbl.setVisible(recording)
        self._b_rec.setChecked(manual)

    def set_selected(self, selected: bool):
        self.setStyleSheet("#video_cell{border:2px solid #ffd84a;}" if selected else "")

    def set_operator(self, can_operate: bool):
        self._b_rec.setVisible(can_operate)

    def _toggle_rec(self):
        self.record_toggled.emit(self.camera_id, not self._manual)

    def _toggle_zoom(self):
        self._zoom_mode = self._b_zoom.isChecked()
        if not self._zoom_mode:
            self._zoom, self._center = 1.0, (0.5, 0.5)
            self._zoom_lbl.setText("")
            self._paint()

    @property
    def zoom(self) -> float:
        return self._zoom

    def zoom_at(self, steps: float, pos: QPoint = None):
        """Digital zoom in (steps > 0) or out around a point of the canvas."""
        if pos is not None and self._canvas.width() and self._canvas.height():
            # Move the zoom centre towards the point under the mouse.
            fx = pos.x() / self._canvas.width()
            fy = pos.y() / self._canvas.height()
            cx, cy = self._center
            span = 1 / self._zoom
            self._center = (min(1, max(0, cx + (fx - 0.5) * span)),
                            min(1, max(0, cy + (fy - 0.5) * span)))
        self._zoom = min(MAX_ZOOM, max(1.0, self._zoom * (1.25 ** steps)))
        if self._zoom == 1.0:
            self._center = (0.5, 0.5)
        self._zoom_lbl.setText(f"Zoom {self._zoom:.1f}×" if self._zoom > 1.0 else "")
        self._paint()

    # ------------------------------------------------------------ mouse
    def wheelEvent(self, event):
        if self._zoom_mode and self.camera_name:
            pos = self._canvas.mapFrom(self, event.position().toPoint())
            self.zoom_at(event.angleDelta().y() / 120, pos)
            event.accept()
            return
        super().wheelEvent(event)

    def enterEvent(self, event):
        self._tools.setVisible(bool(self.camera_name))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._tools.setVisible(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self.window_clicked.emit(self.slot)
            if self.camera_name:
                self.clicked.emit(self.camera_id)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Drag a camera to another window to swap the two.
        if (self._press_pos is not None and self.camera_name and not self._zoom_mode
                and event.buttons() & Qt.MouseButton.LeftButton
                and (event.position().toPoint() - self._press_pos).manhattanLength()
                >= QApplication.startDragDistance()):
            self._press_pos = None
            drag = QDrag(self)
            drag.setMimeData(camera_mime(self.camera_id, self.slot))
            drag.exec(Qt.DropAction.MoveAction)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.camera_name:
            self.double_clicked.emit(self.camera_id)

    def dragEnterEvent(self, event):
        if parse_camera_mime(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        parsed = parse_camera_mime(event.mimeData())
        if parsed and self.slot >= 0:
            self.camera_dropped.emit(self.slot, *parsed)
            event.acceptProposedAction()

    def contextMenuEvent(self, event):
        if not self.camera_name:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#1b1b30;color:#dde1ec;border:1px solid #252550;}"
            "QMenu::item:selected{background:#ef4565;}"
        )
        menu.addAction("Take Snapshot").triggered.connect(
            lambda: self.snapshot_requested.emit(self.camera_id))
        if self._b_rec.isVisible() or not self._tools.isVisible():
            if self._manual:
                menu.addAction("Stop manual recording").triggered.connect(
                    lambda: self.record_toggled.emit(self.camera_id, False))
            else:
                menu.addAction("Start recording").triggered.connect(
                    lambda: self.record_toggled.emit(self.camera_id, True))
        menu.addAction("Instant playback (last 30 s)").triggered.connect(
            lambda: self.playback_requested.emit(self.camera_id))
        menu.addAction("Digital zoom on / off").triggered.connect(
            lambda: (self._b_zoom.toggle(), self._toggle_zoom()))
        menu.addAction("Diagnose connection…").triggered.connect(
            lambda: self.diagnose_requested.emit(self.camera_id))
        menu.addAction("Single view / back to grid  (double-click)").triggered.connect(
            lambda: self.double_clicked.emit(self.camera_id))
        menu.addAction("Full screen  (F11)").triggered.connect(
            lambda: self.fullscreen_requested.emit(self.camera_id))
        menu.addSeparator()
        menu.addAction("Close window").triggered.connect(
            lambda: self.close_requested.emit(self.camera_id))
        menu.exec(event.globalPos())
