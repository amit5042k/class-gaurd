from datetime import datetime

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QStatusBar, QWidget
)

from app.config import Config
from app.core.ai_worker import AIWorker
from app.core.camera_manager import CameraManager
from app.core.face_engine import FaceEngine
from app.core.recording import Recorder
from app.core.stream_manager import StreamManager
from app.ui.pages.alerts import AlertsPage
from app.ui.pages.attendance import AttendancePage
from app.ui.pages.camera_management import CameraManagement
from app.ui.pages.dashboard import Dashboard
from app.ui.pages.expression_monitor import ExpressionMonitor
from app.ui.pages.face_enrollment import FaceEnrollment
from app.ui.pages.live_view import LiveView
from app.ui.pages.nvr_management import NVRManagement
from app.ui.pages.recordings import RecordingsPage
from app.ui.pages.safety_watch import SafetyWatch, load_all_zones
from app.ui.pages.settings import SettingsPage
from app.ui.styles import DARK
from app.ui.widgets.sidebar import Sidebar
from app.ui.widgets.toast import ToastStack


class MainWindow(QMainWindow):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.setWindowTitle(Config.APP_NAME)
        self.setMinimumSize(1280, 780)
        self.resize(1440, 860)
        self.setStyleSheet(DARK)

        self.stream_mgr = StreamManager()
        self.stream_mgr.reconnect_delay = Config.STREAM_RECONNECT_DELAY
        self.face_engine = FaceEngine(Config.FACES_DIR)
        self.recorder = Recorder()
        self.ai = AIWorker(db, self.face_engine)
        self.stream_mgr.add_frame_listener(self.ai.submit_frame)
        self.toasts = ToastStack(self)

        self._build_ui()
        self._connect_signals()
        self._load_data()
        for cid, zones in load_all_zones(db).items():
            self.ai.set_zones(cid, zones)
        if Config.SAFETY_MODEL:
            self.ai.request_model(dict(Config.SAFETY_MODEL))
        self.ai.start()
        self._sidebar.set_active("dashboard")
        if Config.AUTO_START_STREAMS:
            QTimer.singleShot(500, self._p_live.start_all_streams)

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

        self._p_dashboard = Dashboard(self.db)
        self._p_live = LiveView(self.db, self.stream_mgr, self.recorder)
        self._p_cameras = CameraManagement(self.db)
        self._p_nvr = NVRManagement(self.db)
        self._p_faces = FaceEnrollment(self.db, self.face_engine)
        self._p_attend = AttendancePage(self.db)
        self._p_expr = ExpressionMonitor(self.db)
        self._p_safety = SafetyWatch(self.db)
        self._p_alerts = AlertsPage(self.db)
        self._p_rec = RecordingsPage()
        self._p_settings = SettingsPage()

        self._pages = {}
        for key, page in [
            ("dashboard", self._p_dashboard), ("live_view", self._p_live),
            ("cameras", self._p_cameras), ("nvr", self._p_nvr),
            ("face_enrollment", self._p_faces), ("attendance", self._p_attend),
            ("expression", self._p_expr), ("safety", self._p_safety),
            ("alerts", self._p_alerts), ("recordings", self._p_rec),
            ("settings", self._p_settings),
        ]:
            self._stack.addWidget(page)
            self._pages[key] = page

        self._sb_cams = QLabel("Cameras: 0")
        self._sb_live = QLabel("Connected: 0")
        self._sb_ai = QLabel()
        self._sb_time = QLabel()
        sb = QStatusBar()
        sb.addWidget(self._sb_cams)
        sb.addWidget(QLabel(" | "))
        sb.addWidget(self._sb_live)
        sb.addWidget(QLabel(" | "))
        sb.addWidget(self._sb_ai)
        sb.addPermanentWidget(self._sb_time)
        self.setStatusBar(sb)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick_time)
        self._clock.start(1000)
        self._update_ai_label()

    def _connect_signals(self):
        self._sidebar.page_changed.connect(self._navigate)
        self._p_live.active_count_changed.connect(self._on_stream_count)
        self._p_live.message.connect(lambda m: self.statusBar().showMessage(m, 6000))
        self._p_cameras.cameras_changed.connect(self._load_data)
        self._p_faces.faces_changed.connect(self._update_ai_label)
        self._p_settings.settings_saved.connect(self._on_settings_saved)

        for page in (self._p_expr, self._p_safety):
            page.monitor_changed.connect(self._on_monitor_changed)
        self._p_safety.zones_changed.connect(self.ai.set_zones)
        self._p_safety.model_requested.connect(self.ai.request_model)

        self.ai.annotated_frame.connect(self._p_expr.on_frame)
        self.ai.annotated_frame.connect(self._p_safety.on_frame)
        self.ai.expression_result.connect(self._p_expr.on_results)
        self.ai.safety_result.connect(self._p_safety.on_results)
        self.ai.model_status.connect(self._p_safety.on_model_status)
        self.ai.attendance_event.connect(self._on_attendance_event)
        self.ai.alert_created.connect(self._on_alert)

    # ------------------------------------------------------------ handlers
    def _navigate(self, key: str):
        page = self._pages.get(key)
        if page:
            self._stack.setCurrentWidget(page)
            if hasattr(page, "refresh") and key in ("dashboard", "attendance", "alerts"):
                page.refresh()

    def _load_data(self):
        cameras = CameraManager(self.db).get_all_cameras(active_only=True)
        self._p_live.load_cameras(cameras)
        self._p_expr.set_cameras(cameras)
        self._p_safety.set_cameras(cameras)
        self.ai.set_cameras(cameras)
        active_ids = {c["id"] for c in cameras}
        for cid in list(getattr(self, "_known_cams", set()) - active_ids):
            self.ai.clear_camera(cid)
        self._known_cams = active_ids
        self._sidebar.update_camera_count(len(cameras))
        self._sb_cams.setText(f"Cameras: {len(cameras)}")
        self._update_ai_label(cameras)

    def _on_monitor_changed(self, kind: str, camera_id):
        self.ai.set_monitor(kind, camera_id)
        if camera_id is not None and not self.stream_mgr.is_streaming(camera_id):
            if self._p_live.start_camera(camera_id):
                self.statusBar().showMessage("Started the camera stream for monitoring.", 5000)

    def _on_stream_count(self, count: int):
        self._sb_live.setText(f"Connected: {count}")
        self._p_dashboard.set_active_streams(count)

    def _on_attendance_event(self, ev: dict):
        self._p_attend.on_attendance_event(ev)
        if ev.get("type") == "check_in":
            status = "late" if ev.get("late") else "on time"
            self.statusBar().showMessage(
                f"{ev['time']:%H:%M} {ev.get('name')} checked in ({status}) "
                f"at {ev.get('camera_name')}", 10000)
            self._p_dashboard.refresh()

    def _on_alert(self, alert: dict):
        severity = alert.get("severity", "medium")
        if severity in ("high", "medium"):
            self.toasts.show(alert.get("type", "Alert"), alert.get("description", ""),
                             severity, on_click=lambda: self._sidebar.set_active("alerts"))
        if severity == "high" and Config.ALERT_SOUND:
            QApplication.beep()
        if self._stack.currentWidget() is self._p_alerts:
            self._p_alerts.refresh()
        self._p_dashboard.refresh()

    def _on_settings_saved(self):
        self.stream_mgr.reconnect_delay = Config.STREAM_RECONNECT_DELAY
        self._p_attend.refresh()

    def _update_ai_label(self, cameras=None):
        if cameras is None:
            cameras = CameraManager(self.db).get_all_cameras(active_only=True)
        n_att = sum(1 for c in cameras if c.get("attendance_tracking"))
        faces = self.face_engine.known_count
        rec = "" if self.face_engine.recognition_available else " (face recognition not installed)"
        self._sb_ai.setText(f"Attendance cameras: {n_att} · Enrolled faces: {faces}{rec}")

    def _tick_time(self):
        self._sb_time.setText(datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.toasts.relayout()

    def closeEvent(self, event: QCloseEvent):
        self.stream_mgr.stop_all(wait_ms=3000)
        self.ai.stop()
        self.recorder.stop_all()
        event.accept()
