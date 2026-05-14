from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QGroupBox, QFormLayout,
    QSpinBox, QDoubleSpinBox, QCheckBox, QLineEdit,
    QMessageBox
)
from PyQt6.QtCore import Qt
from app.config import Config


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(16)

        title = QLabel("Settings")
        title.setObjectName("page_title")
        root.addWidget(title)

        def lbl(t): l = QLabel(t); l.setObjectName("form_label"); return l

        # Face Recognition
        gb_face = QGroupBox("Face Recognition")
        ff = QFormLayout(gb_face)
        self._threshold = QDoubleSpinBox()
        self._threshold.setRange(0.1, 1.0); self._threshold.setSingleStep(0.05)
        self._threshold.setValue(Config.FACE_RECOGNITION_THRESHOLD)
        ff.addRow(lbl("Recognition Threshold:"), self._threshold)
        root.addWidget(gb_face)

        # Streaming
        gb_stream = QGroupBox("Streaming")
        sf = QFormLayout(gb_stream)
        self._reconnect = QSpinBox()
        self._reconnect.setRange(1, 120); self._reconnect.setValue(Config.STREAM_RECONNECT_DELAY)
        sf.addRow(lbl("Reconnect Delay (s):"), self._reconnect)
        root.addWidget(gb_stream)

        # Recording
        gb_rec = QGroupBox("Recording")
        rf = QFormLayout(gb_rec)
        self._rec_fps = QSpinBox()
        self._rec_fps.setRange(1, 60); self._rec_fps.setValue(Config.RECORDING_FPS)
        rf.addRow(lbl("Recording FPS:"), self._rec_fps)
        root.addWidget(gb_rec)

        # Paths
        gb_paths = QGroupBox("Data Paths (read-only)")
        pf = QFormLayout(gb_paths)
        for lbl_text, path in [
            ("Data Dir:", Config.DATA_DIR),
            ("DB Path:",  Config.DB_PATH),
            ("Faces:",    Config.FACES_DIR),
            ("Recordings:", Config.RECORDINGS_DIR),
            ("Snapshots:",  Config.SNAPSHOTS_DIR),
        ]:
            le = QLineEdit(path); le.setReadOnly(True)
            le.setStyleSheet("color:#666;")
            pf.addRow(lbl(lbl_text), le)
        root.addWidget(gb_paths)

        btn_save = QPushButton("Save Settings")
        btn_save.setObjectName("primary_btn")
        btn_save.setFixedWidth(150)
        btn_save.clicked.connect(self._save)
        root.addWidget(btn_save, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addStretch()

    def _save(self):
        Config.FACE_RECOGNITION_THRESHOLD = self._threshold.value()
        Config.STREAM_RECONNECT_DELAY = self._reconnect.value()
        Config.RECORDING_FPS = self._rec_fps.value()
        QMessageBox.information(self, "Saved",
                                "Settings saved for this session.\n"
                                "For permanent settings, edit app/config.py.")
