from datetime import datetime

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QStatusBar, QWidget
)

from app.config import Config
from app.core.ai_worker import AIWorker
from app.core.camera_manager import CameraManager
from app.core.face_engine import FaceEngine
from app.core.recording import MODE_OFF, RecordingManager, find_recording_at
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
from app.ui.pages.tracking import TrackingPage
from app.ui.pages.users import UsersPage
from app.core.security import ROLE_LABELS, PAGES, CurrentUser, Session, UserService, audit
from app.ui.styles import DARK
from app.ui.widgets.sidebar import Sidebar
from app.ui.widgets.toast import ToastStack


class _Bridge(QObject):
    """Carries callbacks from worker threads onto the UI thread."""
    rec_state = pyqtSignal(int, bool)


class MainWindow(QMainWindow):
    def __init__(self, db, user: CurrentUser = None):
        super().__init__()
        self.db = db
        # Tests and tools may open the window without signing in: act as admin.
        self.user = user or CurrentUser(0, "admin", "admin", "")
        Session.current = self.user
        self._background, self._manual_rec, self._monitor_by_kind = set(), set(), {}
        self.setWindowTitle(f"{Config.APP_NAME}  —  version {Config.VERSION}")
        self.setMinimumSize(1280, 780)
        self.resize(1440, 860)
        self.setStyleSheet(DARK)

        self.stream_mgr = StreamManager()
        self.stream_mgr.reconnect_delay = Config.STREAM_RECONNECT_DELAY
        self.face_engine = FaceEngine(Config.FACES_DIR)
        self._bridge = _Bridge()
        self.recorder = RecordingManager(db, on_state=self._bridge.rec_state.emit)
        self.ai = AIWorker(db, self.face_engine)
        self.stream_mgr.add_frame_listener(self.ai.submit_frame)
        self.stream_mgr.add_frame_listener(self.recorder.on_frame)
        self.toasts = ToastStack(self)

        self._build_ui()
        self._connect_signals()
        self._load_data()
        for cid, zones in load_all_zones(db).items():
            self.ai.set_zones(cid, zones)
        if Config.SAFETY_MODEL:
            self.ai.request_model(dict(Config.SAFETY_MODEL))
        self.ai.start()
        self.recorder.start_cleanup()
        QTimer.singleShot(5000, self._purge_sightings)
        self.apply_user(self.user)
        QTimer.singleShot(500, self._start_background_streams)
        if Config.AUTO_START_STREAMS:
            QTimer.singleShot(600, self._p_live.start_all_streams)

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
        self._p_track = TrackingPage(self.db)
        self._p_alerts = AlertsPage(self.db)
        self._p_rec = RecordingsPage(self.db)
        self._p_settings = SettingsPage(self.db)
        self._p_users = UsersPage(self.db)

        self._pages = {}
        for key, page in [
            ("dashboard", self._p_dashboard), ("live_view", self._p_live),
            ("cameras", self._p_cameras), ("nvr", self._p_nvr),
            ("face_enrollment", self._p_faces), ("attendance", self._p_attend),
            ("expression", self._p_expr), ("safety", self._p_safety),
            ("tracking", self._p_track),
            ("alerts", self._p_alerts), ("recordings", self._p_rec),
            ("settings", self._p_settings), ("users", self._p_users),
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
        self._p_nvr.cameras_changed.connect(self._load_data)
        self._p_nvr.cameras_changed.connect(self._p_cameras.refresh)
        self._p_faces.faces_changed.connect(self._update_ai_label)
        self._p_settings.settings_saved.connect(self._on_settings_saved)
        self._p_settings.restart_requested.connect(self._restart)

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
        self.ai.class_mood.connect(self._p_expr.on_mood)
        self.ai.person_seen.connect(self._on_person_seen)
        self._p_track.live_requested.connect(self._show_camera_live)

        self._bridge.rec_state.connect(self._p_live.set_recording_state)
        self._p_live.record_requested.connect(self._on_record_requested)
        self._p_live.fullscreen_requested.connect(self._toggle_fullscreen)
        self._p_live.playback_requested.connect(self._play_alert_video)
        self._p_alerts.video_requested.connect(self._play_alert_video)
        self._sidebar.lock_requested.connect(self.lock)
        self._sidebar.password_requested.connect(self._change_password)
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)
        QShortcut(QKeySequence("Esc"), self, activated=self._exit_fullscreen)

    # ------------------------------------------------------------ handlers
    # -------------------------------------------------------------- users
    def apply_user(self, user: CurrentUser):
        self.user = user
        Session.current = user
        allowed = PAGES.get(user.role, set())
        self._sidebar.set_allowed(allowed)
        self._sidebar.set_user(user.full_name or user.username,
                               ROLE_LABELS.get(user.role, user.role))
        for page in self._pages.values():
            if hasattr(page, "set_permissions"):
                page.set_permissions(user)
        current = next((k for k, p in self._pages.items()
                        if p is self._stack.currentWidget()), None)
        if current not in allowed or not getattr(self, "_shown_once", False):
            self._shown_once = True
            self._sidebar.set_active("dashboard")

    def lock(self):
        from app.ui.login import LoginDialog
        audit(self.db, "lock", f"{self.user.username} locked the screen")
        self._exit_fullscreen()
        dlg = LoginDialog(UserService(self.db), self, locked=True)
        if dlg.exec() and dlg.user:
            self.apply_user(dlg.user)
        else:
            self.close()

    def _change_password(self):
        from app.ui.login import ChangePasswordDialog
        if self.user.id == 0:
            return
        if ChangePasswordDialog(UserService(self.db), self.user, self).exec():
            audit(self.db, "password_change", f"{self.user.username} changed their password")
            self.statusBar().showMessage("Password changed.", 5000)

    def _navigate(self, key: str):
        if not self.user.can(key):
            return
        page = self._pages.get(key)
        if page:
            self._stack.setCurrentWidget(page)
            if hasattr(page, "refresh") and key in ("dashboard", "attendance", "alerts",
                                                    "users", "recordings"):
                page.refresh()

    def _load_data(self):
        cameras = CameraManager(self.db).get_all_cameras(active_only=True)
        self._p_live.load_cameras(cameras)
        self._p_expr.set_cameras(cameras)
        self._p_safety.set_cameras(cameras)
        self._p_track.set_cameras(cameras)
        self._p_rec.set_cameras(cameras)
        self.ai.set_cameras(cameras)
        self.recorder.set_cameras(cameras)
        active_ids = {c["id"] for c in cameras}
        for cid in list(getattr(self, "_known_cams", set()) - active_ids):
            self.ai.clear_camera(cid)
        self._known_cams = active_ids
        self._sidebar.update_camera_count(len(cameras))
        self._sb_cams.setText(f"Cameras: {len(cameras)}")
        self._update_ai_label(cameras)
        self._cameras = cameras
        if getattr(self, "_started", False):
            self._start_background_streams()

    def _start_background_streams(self):
        """Cameras that record or run AI need their stream even when nobody is
        watching Live View, like an NVR."""
        self._started = True
        pinned = set()
        for cam in self._cameras:
            needed = (cam.get("recording_mode") or MODE_OFF) != MODE_OFF or any(
                cam.get(k) for k in ("attendance_tracking", "face_detection",
                                     "expression_monitor", "safety_watch"))
            if needed:
                pinned.add(cam["id"])
                if not self.stream_mgr.is_streaming(cam["id"]):
                    self._p_live.start_camera(cam["id"], pin=True)
        self._background = pinned
        self._refresh_pins()

    def _on_record_requested(self, camera_id: int, on: bool):
        if not self.user.can_operate:
            return
        self.recorder.set_manual(camera_id, on)
        audit(self.db, "record_start" if on else "record_stop", f"Camera {camera_id}")
        if on:
            self._manual_rec.add(camera_id)
        else:
            self._manual_rec.discard(camera_id)
        self._refresh_pins()
        if on and not self.stream_mgr.is_streaming(camera_id):
            self._p_live.start_camera(camera_id, pin=True)
        self._p_live.set_recording_state(camera_id, self.recorder.is_recording(camera_id) or on)
        self.statusBar().showMessage("Manual recording started." if on
                                     else "Manual recording stopped.", 5000)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self._exit_fullscreen()
            return
        self._sidebar.set_active("live_view")
        self._sidebar.hide()
        self.statusBar().hide()
        self._p_live.set_chrome_visible(False)
        self.showFullScreen()

    def _exit_fullscreen(self):
        if not self.isFullScreen():
            return
        self._sidebar.show()
        self.statusBar().show()
        self._p_live.set_chrome_visible(True)
        self.showNormal()

    def _play_alert_video(self, camera_id: int, when):
        hit = find_recording_at(self.db, camera_id, when)
        if not hit:
            self.statusBar().showMessage(
                "No finished recording covers that time for this camera.", 8000)
            return
        self._sidebar.set_active("recordings")
        self._p_rec.open_recording(camera_id, hit["path"], hit["start"],
                                   max(0.0, hit["offset"] - 5))

    def _refresh_pins(self):
        self._p_live.set_pinned(self._background | self._manual_rec |
                                {c for c in self._monitor_by_kind.values() if c is not None})

    def _on_monitor_changed(self, kind: str, camera_id):
        self.ai.set_monitor(kind, camera_id)
        self._monitor_by_kind[kind] = camera_id
        self._refresh_pins()
        if camera_id is not None and not self.stream_mgr.is_streaming(camera_id):
            if self._p_live.start_camera(camera_id, pin=True):
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
        self._p_live.add_event(alert)
        if severity in ("high", "medium"):
            self.toasts.show(alert.get("type", "Alert"), alert.get("description", ""),
                             severity, on_click=lambda: self._sidebar.set_active("alerts"))
        if severity == "high" and Config.ALERT_SOUND:
            QApplication.beep()
        if self._stack.currentWidget() is self._p_alerts:
            self._p_alerts.refresh()
        self._p_dashboard.refresh()

    def _on_person_seen(self, seen: dict):
        self._p_track.on_person_seen(seen)
        if seen.get("person_id") == self._p_track.tracked_person_id() and \
                self._stack.currentWidget() is not self._p_track:
            self.toasts.show("Person tracking", f"{seen.get('name')} seen at "
                             f"{seen.get('camera_name')}", "info",
                             on_click=lambda: self._sidebar.set_active("tracking"))

    def _show_camera_live(self, camera_id: int):
        self._sidebar.set_active("live_view")
        self._p_live.open_in_window(camera_id)

    def _purge_sightings(self):
        from app.core.tracking import purge_old_sightings
        from app.ui.async_task import run_async
        run_async(purge_old_sightings, lambda *_: None, self.db, receiver=self)

    def _restart(self):
        from app.ui.restart import restart_app
        self.close()
        restart_app()
        QApplication.instance().quit()

    def _on_settings_saved(self):
        audit(self.db, "settings", "Settings saved")
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
        audit(self.db, "exit", f"{self.user.username} closed ClassGuard")
        self._p_live.ptz.shutdown()
        self._p_rec.player.close_file()
        self.stream_mgr.stop_all(wait_ms=3000)
        self.recorder.stop_all()
        self.ai.stop()
        event.accept()
