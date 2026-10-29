from PyQt6.QtCore import Qt, QTime, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTimeEdit,
    QVBoxLayout, QWidget
)

from app.config import Config

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class SettingsPage(QWidget):
    settings_saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.load()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;}")
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        title = QLabel("Settings")
        title.setObjectName("page_title")
        root.addWidget(title)

        gb = QGroupBox("Attendance")
        f = QFormLayout(gb)
        self._shift = QTimeEdit()
        self._shift.setDisplayFormat("HH:mm")
        self._grace = QSpinBox()
        self._grace.setRange(0, 240)
        self._grace.setSuffix(" min")
        days_row = QHBoxLayout()
        self._days = []
        for name in DAY_NAMES:
            cb = QCheckBox(name)
            self._days.append(cb)
            days_row.addWidget(cb)
        days_row.addStretch()
        f.addRow("Shift / school start time:", self._shift)
        f.addRow("Late after:", self._grace)
        f.addRow("Weekly off days:", days_row)
        root.addWidget(gb)

        gb = QGroupBox("Face recognition")
        f = QFormLayout(gb)
        self._threshold = QDoubleSpinBox()
        self._threshold.setRange(0.30, 0.70)
        self._threshold.setSingleStep(0.02)
        self._threshold.setDecimals(2)
        f.addRow("Match threshold:", self._threshold)
        f.addRow("", self._hint("Lower = stricter (fewer wrong matches, more 'Unknown'). "
                                "0.45–0.55 suits most cameras."))
        self._unknown = QCheckBox("Create an alert when an unrecognised face is seen")
        f.addRow("", self._unknown)
        root.addWidget(gb)

        gb = QGroupBox("Alerts")
        f = QFormLayout(gb)
        self._sound = QCheckBox("Play a sound for high-priority alerts")
        self._cooldown = QSpinBox()
        self._cooldown.setRange(5, 3600)
        self._cooldown.setSuffix(" s")
        self._expr_conf = QDoubleSpinBox()
        self._expr_conf.setRange(30, 100)
        self._expr_conf.setSuffix(" %")
        f.addRow("", self._sound)
        f.addRow("Repeat the same alert at most every:", self._cooldown)
        f.addRow("Expression alert confidence:", self._expr_conf)
        root.addWidget(gb)

        gb = QGroupBox("Streaming")
        f = QFormLayout(gb)
        self._autostart = QCheckBox("Start all camera streams when the app opens")
        self._reconnect = QSpinBox()
        self._reconnect.setRange(1, 120)
        self._reconnect.setSuffix(" s")
        f.addRow("", self._autostart)
        f.addRow("First reconnect delay:", self._reconnect)
        f.addRow("", self._hint("AI features only run on cameras whose stream is running."))
        root.addWidget(gb)

        gb = QGroupBox("Data folders (read-only)")
        f = QFormLayout(gb)
        for label, path in (("Data:", Config.DATA_DIR), ("Database:", Config.DB_PATH),
                            ("Snapshots:", Config.SNAPSHOTS_DIR),
                            ("Settings file:", Config.SETTINGS_PATH)):
            le = QLineEdit(path)
            le.setReadOnly(True)
            f.addRow(label, le)
        root.addWidget(gb)

        btn = QPushButton("Save Settings")
        btn.setObjectName("primary_btn")
        btn.setFixedWidth(160)
        btn.clicked.connect(self._save)
        root.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addStretch()

    @staticmethod
    def _hint(text):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#777; font-size:11px;")
        return lbl

    def load(self):
        hh, mm = (str(Config.SHIFT_START).split(":") + ["0"])[:2]
        self._shift.setTime(QTime(int(hh), int(mm)))
        self._grace.setValue(int(Config.LATE_GRACE_MINUTES))
        for i, cb in enumerate(self._days):
            cb.setChecked(i in Config.WEEKLY_OFF_DAYS)
        self._threshold.setValue(float(Config.FACE_RECOGNITION_THRESHOLD))
        self._unknown.setChecked(bool(Config.ALERT_UNKNOWN_FACES))
        self._sound.setChecked(bool(Config.ALERT_SOUND))
        self._cooldown.setValue(int(Config.ALERT_COOLDOWN))
        self._expr_conf.setValue(float(Config.EXPRESSION_ALERT_CONFIDENCE))
        self._autostart.setChecked(bool(Config.AUTO_START_STREAMS))
        self._reconnect.setValue(int(Config.STREAM_RECONNECT_DELAY))

    def _save(self):
        Config.SHIFT_START = self._shift.time().toString("HH:mm")
        Config.LATE_GRACE_MINUTES = self._grace.value()
        Config.WEEKLY_OFF_DAYS = [i for i, cb in enumerate(self._days) if cb.isChecked()]
        Config.FACE_RECOGNITION_THRESHOLD = round(self._threshold.value(), 2)
        Config.ALERT_UNKNOWN_FACES = self._unknown.isChecked()
        Config.ALERT_SOUND = self._sound.isChecked()
        Config.ALERT_COOLDOWN = self._cooldown.value()
        Config.EXPRESSION_ALERT_CONFIDENCE = self._expr_conf.value()
        Config.AUTO_START_STREAMS = self._autostart.isChecked()
        Config.STREAM_RECONNECT_DELAY = self._reconnect.value()
        try:
            Config.save_settings()
        except OSError as e:
            QMessageBox.critical(self, "Not saved", f"Could not save settings: {e}")
            return
        self.settings_saved.emit()
        QMessageBox.information(self, "Saved", "Settings saved.")
