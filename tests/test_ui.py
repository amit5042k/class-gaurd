"""Widget-level tests (run headless with QT_QPA_PLATFORM=offscreen)."""
import csv
import json

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from app.config import Config
from tests.conftest import needs_face


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


def test_camera_dialog_auto_url_and_edit(qtbot):
    from app.core.camera_manager import CameraManager
    from app.ui.pages.camera_management import CameraDialog
    saved = {"id": 1, "name": "Gate", "brand": "Dahua", "host": "10.0.0.1", "port": 554,
             "username": "admin", "password": "pw", "channel": 2, "use_substream": False}
    saved["rtsp_url"] = CameraManager.auto_url(saved)
    dlg = CameraDialog(saved)
    qtbot.addWidget(dlg)
    assert dlg._rtsp.text() == ""                    # auto URL is not frozen into the field
    assert "pw" not in dlg._url_hint.text()          # password is masked in the hint
    dlg._host.setText("10.0.0.9")
    data = dlg.get_data()
    assert data["host"] == "10.0.0.9" and data["rtsp_url"] is None

    custom = dict(saved, rtsp_url="rtsp://10.0.0.1/custom")
    dlg2 = CameraDialog(custom)
    qtbot.addWidget(dlg2)
    assert dlg2.get_data()["rtsp_url"] == "rtsp://10.0.0.1/custom"


def test_camera_dialog_test_button_runs_in_background(qtbot):
    from app.ui.pages.camera_management import CameraDialog
    dlg = CameraDialog()
    qtbot.addWidget(dlg)
    dlg._rtsp.setText("rtsp://127.0.0.1:1/nothing")
    dlg._test_conn()
    assert not dlg._test_btn.isEnabled()             # UI stays responsive while testing
    qtbot.waitUntil(lambda: dlg._test_btn.isEnabled(), timeout=20000)
    assert dlg._test_result.text().startswith("Failed")


def test_zone_canvas_maps_clicks_to_frame_pixels(qtbot):
    from app.ui.widgets.zone_canvas import VideoCanvas
    canvas = VideoCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(500, 500)
    img = QImage(1000, 500, QImage.Format.Format_RGB888)
    img.fill(0)
    canvas.set_image(img)
    canvas.start_drawing()
    # Image is shown 500x250 centred vertically (offset 125).
    for x, y in ((250, 250), (0, 125), (499, 374)):
        qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(x, y))
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(250, 10))  # outside
    pts = canvas.stop_drawing()
    assert pts == [(500, 250), (0, 0), (998, 498)]


def test_safety_page_saves_zone(qtbot, db):
    from app.ui.pages.safety_watch import SafetyWatch, load_zones
    page = SafetyWatch(db)
    qtbot.addWidget(page)
    page.resize(1000, 700)
    page.show()
    page.set_cameras([{"id": 4, "name": "Lab"}])
    emitted = []
    page.monitor_changed.connect(lambda k, c: emitted.append(("monitor", k, c)))
    page.zones_changed.connect(lambda c, z: emitted.append(("zones", c, z)))
    page._toggle()
    img = QImage(640, 480, QImage.Format.Format_RGB888)
    img.fill(0)
    page.on_frame("safety", 4, img)
    assert page._btn_draw.isEnabled()
    page._start_zone()
    page._video._points = [(10, 10), (300, 10), (300, 300)]
    page._finish_zone()
    assert load_zones(db, 4) == [[[10, 10], [300, 10], [300, 300]]]
    assert ("monitor", "safety", 4) in emitted and emitted[-1][0] == "zones"
    page._clear_zones()
    assert load_zones(db, 4) == []


def test_settings_page_persists(qtbot):
    from app.ui.pages.settings import SettingsPage
    page = SettingsPage()
    qtbot.addWidget(page)
    page._grace.setValue(25)
    page._days[5].setChecked(True)
    page._save()
    with open(Config.SETTINGS_PATH) as f:
        data = json.load(f)
    assert data["LATE_GRACE_MINUTES"] == 25 and data["WEEKLY_OFF_DAYS"] == [5, 6]


def test_attendance_month_export(qtbot, db, tmp_path, monkeypatch):
    from datetime import datetime
    from app.core.attendance_engine import AttendanceEngine
    from app.models import Person
    from app.ui.pages.attendance import AttendancePage
    s = db.get_session()
    p = Person(name="Ravi", employee_id="S-01")
    s.add(p)
    s.commit()
    s.close()
    AttendanceEngine(db).process(1, None, [{"person_id": p.id, "name": "Ravi",
                                            "confidence": .9}],
                                 now=datetime.now().replace(hour=8, minute=30))
    page = AttendancePage(db)
    qtbot.addWidget(page)
    assert page._table.rowCount() == 1 and page._table.item(0, 3).text() == "Present"
    out = tmp_path / "month.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    page._export_month()
    rows = list(csv.reader(open(out, encoding="utf-8-sig")))
    assert rows[0][:7] == ["Name", "Employee ID", "Department", "Present", "Late",
                           "Absent", "Hours"]
    assert rows[1][0] == "Ravi" and rows[1][3] == "1"


@needs_face
def test_enroll_from_photo_file(qtbot, db, face_image, tmp_path, monkeypatch):
    import cv2
    from app.core.face_engine import FaceEngine
    from app.models import Person
    from app.ui.pages.face_enrollment import FaceEnrollment
    s = db.get_session()
    s.add(Person(name="Astro"))
    s.commit()
    s.close()
    fe = FaceEngine(str(tmp_path / "faces"))
    page = FaceEnrollment(db, fe)
    qtbot.addWidget(page)
    page._table.selectRow(0)
    photo = tmp_path / "astro.jpg"
    cv2.imwrite(str(photo), face_image)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(photo), "")))
    page._enroll_from_file()
    qtbot.waitUntil(lambda: fe.known_count == 1, timeout=20000)
    assert page._table.item(0, 5).text() == "1"
    page._enroll_from_file()                          # second photo is added, not replaced
    qtbot.waitUntil(lambda: page._table.item(0, 5).text() == "2", timeout=20000)


def test_main_window_starts_and_every_page_opens(qtbot, db, monkeypatch):
    from app.core.camera_manager import CameraManager
    from app.ui.main_window import MainWindow
    CameraManager(db).add_camera({"name": "Gate", "brand": "Hikvision", "host": "127.0.0.1",
                                  "port": 1, "attendance_tracking": True})
    w = MainWindow(db)
    qtbot.addWidget(w)
    w.show()
    try:
        for key in ("live_view", "cameras", "nvr", "face_enrollment", "attendance",
                    "expression", "safety", "alerts", "recordings", "settings", "dashboard"):
            w._sidebar.set_active(key)
            assert w._stack.currentWidget() is w._pages[key]
        assert "Attendance cameras: 1" in w._sb_ai.text()
        w._on_alert({"type": "Zone intrusion", "description": "test", "severity": "high"})
        assert len(w.toasts._toasts) == 1
    finally:
        w.close()


def test_version_is_shown(qtbot, db):
    from app.ui.login import LoginDialog
    from app.core.security import UserService
    from app.ui.main_window import MainWindow
    w = MainWindow(db)
    qtbot.addWidget(w)
    try:
        assert Config.VERSION in w.windowTitle()
        assert f"v{Config.VERSION}" in w._sidebar.findChildren(type(w._sb_cams))[1].text()
    finally:
        w.close()
    dlg = LoginDialog(UserService(db))
    qtbot.addWidget(dlg)
    assert Config.VERSION in dlg.findChildren(type(w._sb_cams))[0].text()
