from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QMenu, QSizePolicy, QVBoxLayout, QWidget


class VideoCell(QWidget):
    double_clicked = pyqtSignal(int)   # camera_id
    clicked = pyqtSignal(int)
    snapshot_requested = pyqtSignal(int)
    fullscreen_requested = pyqtSignal(int)
    record_toggled = pyqtSignal(int, bool)   # camera_id, start?
    diagnose_requested = pyqtSignal(int)

    def __init__(self, camera_id: int, camera_name: str, parent=None):
        super().__init__(parent)
        self.camera_id = camera_id
        self.camera_name = camera_name
        self._connected = False
        self._recording = False
        self._manual = False
        self.setObjectName("video_cell")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar
        top = QWidget()
        top.setObjectName("cam_name_bar")
        top.setFixedHeight(24)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(6, 0, 6, 0)

        self._status_dot = QLabel("●")
        self._status_dot.setObjectName("disconnected_dot")
        self._name_lbl = QLabel(self.camera_name)
        self._name_lbl.setObjectName("cam_name_bar")
        self._rec_lbl = QLabel("● REC")
        self._rec_lbl.setObjectName("recording_dot")
        self._rec_lbl.setStyleSheet("color:#e74c3c; font-size:10px;")
        self._rec_lbl.setVisible(False)

        self._status_dot.setVisible(bool(self.camera_name))
        tl.addWidget(self._status_dot)
        tl.addWidget(self._name_lbl)
        tl.addStretch()
        tl.addWidget(self._rec_lbl)
        root.addWidget(top)

        # Video canvas
        self._canvas = QLabel()
        self._canvas.setObjectName("video_canvas")
        self._canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Ignored: otherwise each scaled frame enlarges the label's size hint
        # and the tiles keep growing.
        self._canvas.setSizePolicy(QSizePolicy.Policy.Ignored,
                                   QSizePolicy.Policy.Ignored)
        self._canvas.setMinimumSize(1, 1)
        self._canvas.setWordWrap(True)
        self._canvas.setText("No Signal")
        self._canvas.setStyleSheet("color:#444; font-size:13px;")
        root.addWidget(self._canvas)

    # ----------------------------------------------------------------- slots
    def update_frame(self, camera_id: int, img: QImage):
        if camera_id != self.camera_id:
            return
        if not self._connected:
            self.set_status(True)
        pixmap = QPixmap.fromImage(img)
        self._canvas.setPixmap(
            pixmap.scaled(self._canvas.size(),
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.FastTransformation)
        )

    def set_status(self, connected: bool, msg: str = ""):
        self._connected = connected
        if connected:
            self._status_dot.setObjectName("connected_dot")
            self._status_dot.setStyleSheet("color:#27ae60; font-size:14px;")
            self._canvas.setStyleSheet("")
        else:
            self._status_dot.setObjectName("disconnected_dot")
            self._canvas.setPixmap(QPixmap())  # clears text too, so do it first
            connecting = msg.startswith("Connecting")
            self._canvas.setText(msg if connecting else f"Disconnected\n{msg}")
            self._status_dot.setStyleSheet(
                f"color:{'#f39c12' if connecting else '#ef4565'}; font-size:14px;")
            self._canvas.setStyleSheet("color:#555; font-size:12px;")

    def set_recording(self, recording: bool, manual: bool = False):
        self._recording = recording
        self._manual = manual
        self._rec_lbl.setVisible(recording)

    def set_selected(self, selected: bool):
        self.setStyleSheet("#video_cell{border:2px solid #ffd84a;}" if selected else "")

    def mousePressEvent(self, event):
        if self.camera_name and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.camera_id)
        super().mousePressEvent(event)

    # ------------------------------------------------------------ mouse
    def mouseDoubleClickEvent(self, event):
        if self.camera_name:
            self.double_clicked.emit(self.camera_id)

    def contextMenuEvent(self, event):
        if not self.camera_name:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#1b1b30;color:#dde1ec;border:1px solid #252550;}"
            "QMenu::item:selected{background:#ef4565;}"
        )
        menu.addAction("Take Snapshot").triggered.connect(
            lambda: self.snapshot_requested.emit(self.camera_id)
        )
        if self._manual:
            menu.addAction("Stop manual recording").triggered.connect(
                lambda: self.record_toggled.emit(self.camera_id, False))
        else:
            menu.addAction("Start recording").triggered.connect(
                lambda: self.record_toggled.emit(self.camera_id, True))
        menu.addAction("Diagnose connection…").triggered.connect(
            lambda: self.diagnose_requested.emit(self.camera_id))
        menu.addAction("Single view / back to grid  (double-click)").triggered.connect(
            lambda: self.double_clicked.emit(self.camera_id))
        menu.addAction("Full screen  (F11)").triggered.connect(
            lambda: self.fullscreen_requested.emit(self.camera_id))
        menu.exec(event.globalPos())
