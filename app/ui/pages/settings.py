from PyQt6.QtCore import Qt, QTime, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTimeEdit,
    QVBoxLayout, QWidget
)

from app.config import Config

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class SettingsPage(QWidget):
    settings_saved = pyqtSignal()
    restart_requested = pyqtSignal()

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.db = db
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
        f.addRow("", self._hint("Cameras that record or use an AI feature always connect "
                                "automatically; this option starts the others too."))
        root.addWidget(gb)

        gb = QGroupBox("Recording")
        f = QFormLayout(gb)
        self._keep_days = QSpinBox()
        self._keep_days.setRange(1, 3650)
        self._keep_days.setSuffix(" days")
        self._max_gb = QDoubleSpinBox()
        self._max_gb.setRange(1, 100000)
        self._max_gb.setDecimals(0)
        self._max_gb.setSuffix(" GB")
        self._min_free = QDoubleSpinBox()
        self._min_free.setRange(0, 1000)
        self._min_free.setDecimals(0)
        self._min_free.setSuffix(" GB")
        self._segment = QSpinBox()
        self._segment.setRange(1, 60)
        self._segment.setSuffix(" min")
        self._res = QComboBox()
        for label, w in (("Original", 0), ("1920 wide (1080p)", 1920),
                         ("1280 wide (720p)", 1280), ("960 wide", 960), ("640 wide", 640)):
            self._res.addItem(label, w)
        self._sens = QComboBox()
        for label, v in (("Low (big movements only)", "low"), ("Medium", "medium"),
                         ("High (small movements)", "high")):
            self._sens.addItem(label, v)
        self._post = QSpinBox()
        self._post.setRange(2, 120)
        self._post.setSuffix(" s")
        f.addRow("Keep recordings for:", self._keep_days)
        f.addRow("Use at most:", self._max_gb)
        f.addRow("Always leave free on disk:", self._min_free)
        f.addRow("File length:", self._segment)
        f.addRow("Recording resolution:", self._res)
        f.addRow("Motion sensitivity:", self._sens)
        f.addRow("Keep recording after motion stops:", self._post)
        f.addRow("", self._hint("The oldest recordings are deleted automatically when "
                                "any of these limits is reached. Recording mode is set "
                                "per camera in Camera Management."))
        root.addWidget(gb)

        gb = QGroupBox("Backup && restore")
        f = QVBoxLayout(gb)
        self._auto_backup = QCheckBox("Make an automatic backup every day (the last 7 are kept)")
        f.addWidget(self._auto_backup)
        row = QHBoxLayout()
        for text, name, fn in (("Back up now…", "primary_btn", self._backup_now),
                               ("Restore from backup…", "secondary_btn", self._restore),
                               ("Open backups folder", "secondary_btn", self._open_backups)):
            b = QPushButton(text)
            b.setObjectName(name)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        f.addLayout(row)
        f.addWidget(self._hint("Backups contain the database, settings, enrolled face "
                               "photos and the key that unlocks saved camera passwords. "
                               "Keep them somewhere safe. Recordings are not included."))
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
        self._auto_backup.setChecked(bool(Config.AUTO_BACKUP))
        self._keep_days.setValue(int(Config.RECORDING_RETENTION_DAYS))
        self._max_gb.setValue(float(Config.RECORDING_MAX_GB))
        self._min_free.setValue(float(Config.RECORDING_MIN_FREE_GB))
        self._segment.setValue(int(Config.RECORDING_SEGMENT_MINUTES))
        self._res.setCurrentIndex(max(0, self._res.findData(int(Config.RECORDING_MAX_WIDTH))))
        self._sens.setCurrentIndex(max(0, self._sens.findData(Config.MOTION_SENSITIVITY)))
        self._post.setValue(int(Config.MOTION_POST_ROLL))

    # ---------------------------------------------------------------- backup
    def _backup_now(self):
        import os
        from datetime import datetime
        from PyQt6.QtWidgets import QFileDialog
        from app.core.backup import create_backup
        from app.ui.async_task import run_async
        default = os.path.join(os.path.expanduser("~"),
                               f"classguard_backup_{datetime.now():%Y%m%d_%H%M}.zip")
        path, _ = QFileDialog.getSaveFileName(self, "Save backup", default, "Zip files (*.zip)")
        if not path:
            return

        def done(result, error):
            if error:
                QMessageBox.critical(self, "Backup failed", str(error))
            else:
                from app.core.security import audit
                audit(getattr(self, "db", None), "backup", result)
                QMessageBox.information(self, "Backup saved", f"Backup saved to\n{result}")

        run_async(create_backup, done, path, receiver=self)

    def _restore(self):
        from PyQt6.QtWidgets import QFileDialog
        from app.core.backup import stage_restore
        path, _ = QFileDialog.getOpenFileName(self, "Choose a backup", "", "Zip files (*.zip)")
        if not path:
            return
        try:
            from app.core.backup import inspect_backup
            m = inspect_backup(path)
        except ValueError as e:
            QMessageBox.warning(self, "Can't restore", str(e))
            return
        warn = "" if m.get("has_key") else ("\n\nThis backup has no key file, so saved "
                                            "camera passwords will need to be re-entered.")
        if QMessageBox.question(
                self, "Restore backup",
                f"Restore the backup made on {m.get('created', '?')} "
                f"(version {m.get('app_version', '?')})?\n\nAll current cameras, people, "
                f"attendance and settings are replaced. The current data is kept in a "
                f"separate folder.{warn}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        stage_restore(path)
        from app.core.security import audit
        audit(getattr(self, "db", None), "restore", f"Staged restore from {path}")
        if QMessageBox.question(self, "Restart needed",
                                "The backup will be restored when ClassGuard restarts. "
                                "Restart now?",
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.restart_requested.emit()

    def _open_backups(self):
        import os
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from app.core.backup import backups_dir
        os.makedirs(backups_dir(), exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(backups_dir()))

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
        Config.AUTO_BACKUP = self._auto_backup.isChecked()
        Config.RECORDING_RETENTION_DAYS = self._keep_days.value()
        Config.RECORDING_MAX_GB = self._max_gb.value()
        Config.RECORDING_MIN_FREE_GB = self._min_free.value()
        Config.RECORDING_SEGMENT_MINUTES = self._segment.value()
        Config.RECORDING_MAX_WIDTH = self._res.currentData()
        Config.MOTION_SENSITIVITY = self._sens.currentData()
        Config.MOTION_POST_ROLL = self._post.value()
        try:
            Config.save_settings()
        except OSError as e:
            QMessageBox.critical(self, "Not saved", f"Could not save settings: {e}")
            return
        self.settings_saved.emit()
        QMessageBox.information(self, "Saved", "Settings saved.")
