from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QStackedWidget,
    QStatusBar, QLabel, QSplitter, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCloseEvent

from app.ui.styles import DARK
from app.ui.widgets.sidebar import Sidebar
from app.ui.pages.dashboard import Dashboard
from app.ui.pages.live_view import LiveView
from app.ui.pages.camera_management import CameraManagement
from app.ui.pages.nvr_management import NVRManagement
from app.ui.pages.face_enrollment import FaceEnrollment
from app.ui.pages.attendance import AttendancePage
from app.ui.pages.expression_monitor import ExpressionMonitor
from app.ui.pages.safety_watch import SafetyWatch
from app.ui.pages.alerts import AlertsPage
from app.ui.pages.recordings import RecordingsPage
from app.ui.pages.settings import SettingsPage

from app.core.stream_manager import StreamManager
from app.core.face_engine import FaceEngine
from app.core.attendance_engine import AttendanceEngine
from app.core.recording import Recorder
from app.config import Config


class MainWindow(QMainWindow):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.setWindowTitle(Config.APP_NAME)
        self.setMinimumSize(1280, 780)
        self.resize(1440, 860)
        self.setStyleSheet(DARK)

        # Shared services
        self.stream_mgr = StreamManager()
        self.face_engine = FaceEngine(Config.FACES_DIR)
        self.recorder = Recorder()
        self.att_engine = AttendanceEngine(db, self.face_engine)

        self._build_ui()
        self._connect_signals()
        self._load_data()
        self._sidebar.set_active("dashboard")

    # ----------------------------------------------------------------- build
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._sidebar = Sidebar()
        main_layout.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        main_layout.addWidget(self._stack)

        # Instantiate pages
        self._pages = {}

        self._p_dashboard = Dashboard(self.db)
        self._p_live      = LiveView(self.db, self.stream_mgr, self.recorder)
        self._p_cameras   = CameraManagement(self.db)
        self._p_nvr       = NVRManagement(self.db)
        self._p_faces     = FaceEnrollment(self.db, self.face_engine)
        self._p_attend    = AttendancePage(self.db, self.att_engine)
        self._p_expr      = ExpressionMonitor(self.db, self.stream_mgr)
        self._p_safety    = SafetyWatch(self.db, self.stream_mgr)
        self._p_alerts    = AlertsPage(self.db)
        self._p_rec       = RecordingsPage()
        self._p_settings  = SettingsPage()

        for key, page in [
            ("dashboard",       self._p_dashboard),
            ("live_view",       self._p_live),
            ("cameras",         self._p_cameras),
            ("nvr",             self._p_nvr),
            ("face_enrollment", self._p_faces),
            ("attendance",      self._p_attend),
            ("expression",      self._p_expr),
            ("safety",          self._p_safety),
            ("alerts",          self._p_alerts),
            ("recordings",      self._p_rec),
            ("settings",        self._p_settings),
        ]:
            self._stack.addWidget(page)
            self._pages[key] = page

        # Status bar
        self._sb_cams  = QLabel("Cameras: 0")
        self._sb_live  = QLabel("Streams: 0")
        self._sb_time  = QLabel()
        sb = QStatusBar()
        sb.addWidget(self._sb_cams)
        sb.addWidget(QLabel(" | "))
        sb.addWidget(self._sb_live)
        sb.addPermanentWidget(self._sb_time)
        self.setStatusBar(sb)
        QTimer(self, timeout=self._tick_time).start(1000)

    def _connect_signals(self):
        self._sidebar.page_changed.connect(self._navigate)
        self._p_live.active_count_changed.connect(self._on_stream_count)
        self._p_cameras.refresh  # triggered internally after add/edit/del

    def _navigate(self, key: str):
        page = self._pages.get(key)
        if page:
            self._stack.setCurrentWidget(page)
            if key == "live_view":
                self._refresh_live_view()
            elif key == "expression":
                self._p_expr.set_cameras(self._get_cameras())
            elif key == "safety":
                self._p_safety.set_cameras(self._get_cameras())

    def _load_data(self):
        cameras = self._get_cameras()
        self._p_live.load_cameras(cameras)
        self._p_expr.set_cameras(cameras)
        self._p_safety.set_cameras(cameras)
        total = len(cameras)
        self._sidebar.update_camera_count(total)
        self._sb_cams.setText(f"Cameras: {total}")

    def _refresh_live_view(self):
        cameras = self._get_cameras()
        self._p_live.load_cameras(cameras)

    def _get_cameras(self):
        from app.core.camera_manager import CameraManager
        return CameraManager(self.db).get_all_cameras(active_only=True)

    def _on_stream_count(self, count: int):
        self._sb_live.setText(f"Streams: {count}")
        self._p_dashboard.set_active_streams(count)

    def _tick_time(self):
        from datetime import datetime
        self._sb_time.setText(datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))

    def closeEvent(self, event: QCloseEvent):
        self.stream_mgr.stop_all()
        self.recorder.stop_all()
        event.accept()
