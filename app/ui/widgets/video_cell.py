from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QMenu
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QPixmap, QImage, QCursor


class VideoCell(QWidget):
    double_clicked = pyqtSignal(int)   # camera_id
    snapshot_requested = pyqtSignal(int)
    fullscreen_requested = pyqtSignal(int)

    def __init__(self, camera_id: int, camera_name: str, parent=None):
        super().__init__(parent)
        self.camera_id = camera_id
        self.camera_name = camera_name
        self._connected = False
        self._recording = False
        self.setObjectName("video_cell")
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

        tl.addWidget(self._status_dot)
        tl.addWidget(self._name_lbl)
        tl.addStretch()
        tl.addWidget(self._rec_lbl)
        root.addWidget(top)

        # Video canvas
        self._canvas = QLabel()
        self._canvas.setObjectName("video_canvas")
        self._canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Expanding)
        self._canvas.setText("No Signal")
        self._canvas.setStyleSheet("color:#444; font-size:13px;")
        root.addWidget(self._canvas)

    # ----------------------------------------------------------------- slots
    def update_frame(self, camera_id: int, img: QImage):
        if camera_id != self.camera_id:
            return
        pixmap = QPixmap.fromImage(img)
        self._canvas.setPixmap(
            pixmap.scaled(self._canvas.size(),
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        )

    def set_status(self, connected: bool, msg: str = ""):
        self._connected = connected
        if connected:
            self._status_dot.setObjectName("connected_dot")
            self._status_dot.setStyleSheet("color:#27ae60; font-size:14px;")
            self._canvas.setStyleSheet("")
        else:
            self._status_dot.setObjectName("disconnected_dot")
            self._status_dot.setStyleSheet("color:#ef4565; font-size:14px;")
            self._canvas.setText(f"Disconnected\n{msg}")
            self._canvas.setStyleSheet("color:#555; font-size:12px;")
            self._canvas.setPixmap(QPixmap())

    def set_recording(self, recording: bool):
        self._recording = recording
        self._rec_lbl.setVisible(recording)

    # ------------------------------------------------------------ mouse
    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit(self.camera_id)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#1b1b30;color:#dde1ec;border:1px solid #252550;}"
            "QMenu::item:selected{background:#ef4565;}"
        )
        menu.addAction("Take Snapshot").triggered.connect(
            lambda: self.snapshot_requested.emit(self.camera_id)
        )
        menu.addAction("Fullscreen").triggered.connect(
            lambda: self.fullscreen_requested.emit(self.camera_id)
        )
        menu.exec(event.globalPos())
