import sqlite3
from datetime import date, datetime, timedelta

import numpy as np
import pytest

from app.config import Config


# --------------------------------------------------------------- RTSP URLs
@pytest.mark.parametrize("brand,sub,expected", [
    ("Hikvision", False, "rtsp://admin:pw@10.0.0.5:554/Streaming/Channels/301"),
    ("Hikvision", True, "rtsp://admin:pw@10.0.0.5:554/Streaming/Channels/302"),
    ("Dahua", False, "rtsp://admin:pw@10.0.0.5:554/cam/realmonitor?channel=3&subtype=0"),
    ("Dahua", True, "rtsp://admin:pw@10.0.0.5:554/cam/realmonitor?channel=3&subtype=1"),
    ("CP Plus", False, "rtsp://admin:pw@10.0.0.5:554/cam/realmonitor?channel=3&subtype=0"),
    ("UNV", False, "rtsp://admin:pw@10.0.0.5:554/unicast/c3/s0/live"),
    ("UNV", True, "rtsp://admin:pw@10.0.0.5:554/unicast/c3/s1/live"),
])
def test_brand_urls(brand, sub, expected):
    assert Config.build_rtsp_url(brand, "10.0.0.5", 554, "admin", "pw", 3, sub) == expected


def test_password_special_characters_are_escaped_only_when_needed():
    url = Config.build_rtsp_url("Hikvision", "h", 554, "admin", "Ab@1:x/y#z!$", 1)
    assert url.startswith("rtsp://admin:Ab%401%3Ax%2Fy%23z!$@h:554/")
    plain = Config.build_rtsp_url("Hikvision", "h", 554, "admin", "Abc123!$", 1)
    assert "Abc123!$@h" in plain


def test_settings_roundtrip():
    Config.SHIFT_START = "08:15"
    Config.WEEKLY_OFF_DAYS = [5, 6]
    Config.save_settings()
    Config.SHIFT_START, Config.WEEKLY_OFF_DAYS = "09:00", [6]
    Config.load_settings()
    assert Config.SHIFT_START == "08:15" and Config.WEEKLY_OFF_DAYS == [5, 6]


# ---------------------------------------------------------- DB migration
def test_old_database_gets_new_columns(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE cameras (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL,"
                " brand VARCHAR(50) NOT NULL, host VARCHAR(150) NOT NULL, rtsp_url VARCHAR(600))")
    con.execute("INSERT INTO cameras (name, brand, host) VALUES ('Gate', 'Dahua', '1.2.3.4')")
    con.commit()
    con.close()
    from app.database import Database
    from app.models import Camera
    db = Database(f"sqlite:///{path}")
    db.init()
    s = db.get_session()
    cam = s.query(Camera).one()
    assert cam.name == "Gate" and cam.use_substream in (False, 0)
    s.close()


# ------------------------------------------------------- camera manager
def test_editing_ip_rebuilds_auto_url_but_keeps_custom(db):
    from app.core.camera_manager import CameraManager
    mgr = CameraManager(db)
    cam = mgr.add_camera({"name": "A", "brand": "Hikvision", "host": "10.0.0.1",
                          "username": "admin", "password": "x", "channel": 1})
    assert "10.0.0.1" in mgr.get_camera(cam.id)["rtsp_url"]
    mgr.update_camera(cam.id, {"host": "10.0.0.2", "rtsp_url": None})
    assert "10.0.0.2" in mgr.get_camera(cam.id)["rtsp_url"]
    mgr.update_camera(cam.id, {"rtsp_url": "rtsp://custom/x"})
    mgr.update_camera(cam.id, {"host": "10.0.0.3", "rtsp_url": "rtsp://custom/x"})
    assert mgr.get_camera(cam.id)["rtsp_url"] == "rtsp://custom/x"


# ----------------------------------------------------------- attendance
def _person(db, name="Asha", emp=None):
    from app.models import Person
    s = db.get_session()
    p = Person(name=name, employee_id=emp)
    s.add(p)
    s.commit()
    s.close()
    return p.id


def test_one_record_per_day_and_checkout_is_last_seen(db):
    from app.core.attendance_engine import AttendanceEngine
    from app.models import AttendanceRecord
    pid = _person(db)
    eng = AttendanceEngine(db)
    t0 = datetime(2026, 3, 2, 8, 50)
    face = [{"person_id": pid, "name": "Asha", "confidence": 0.8}]
    ev = eng.process(1, None, face, now=t0)
    assert ev[0]["type"] == "check_in" and ev[0]["late"] is False
    assert eng.process(2, None, face, now=t0 + timedelta(seconds=5)) == []  # throttled
    eng.process(2, None, face, now=t0 + timedelta(hours=3))   # other camera
    eng.process(1, None, face, now=t0 + timedelta(hours=7))
    s = db.get_session()
    recs = s.query(AttendanceRecord).all()
    assert len(recs) == 1
    assert recs[0].check_in == t0 and recs[0].check_out == t0 + timedelta(hours=7)
    s.close()


def test_daily_report_late_absent_and_weekly_off(db):
    from app.core.attendance_engine import AttendanceEngine
    Config.SHIFT_START, Config.LATE_GRACE_MINUTES = "09:00", 10
    on_time, late = _person(db, "A"), _person(db, "B")
    _person(db, "C")  # never seen -> absent
    eng = AttendanceEngine(db)
    day = date(2026, 3, 2)  # a Monday
    eng.process(1, None, [{"person_id": on_time, "name": "A", "confidence": .9}],
                now=datetime.combine(day, datetime.min.time()).replace(hour=9, minute=5))
    eng.process(1, None, [{"person_id": late, "name": "B", "confidence": .9}],
                now=datetime.combine(day, datetime.min.time()).replace(hour=9, minute=11))
    s = db.get_session()
    rows = {r["name"]: r["status"] for r in AttendanceEngine.get_report(s, day.isoformat())}
    assert rows == {"A": "Present", "B": "Late", "C": "Absent"}
    # Sunday is a weekly off day by default: nobody is listed as absent.
    sunday = AttendanceEngine.get_report(s, "2026-03-01")
    assert sunday == []
    days, monthly = AttendanceEngine.get_monthly_report(s, 2026, 3)
    s.close()
    assert len(days) == 31
    a = next(r for r in monthly if r["name"] == "A")
    b = next(r for r in monthly if r["name"] == "B")
    assert a["days"][1] == "P" and b["days"][1] == "L" and a["days"][0] == "O"
    assert a["present"] == 1 and b["late"] == 1


# ----------------------------------------------------------------- alerts
def test_alert_cooldown_and_violation_saved(db):
    from app.core.alerts import AlertService
    from app.models import Alert, SafetyViolation
    Config.ALERT_COOLDOWN = 60
    svc = AlertService(db)
    frame = np.zeros((10, 10, 3), np.uint8)
    a1 = svc.raise_alert(1, "Zone intrusion", "x", "high", frame, key=(1, "zone"),
                         violation_type="zone_intrusion")
    a2 = svc.raise_alert(1, "Zone intrusion", "x", "high", frame, key=(1, "zone"))
    a3 = svc.raise_alert(2, "Zone intrusion", "x", "high", frame, key=(2, "zone"))
    assert a1 and a2 is None and a3
    s = db.get_session()
    assert s.query(Alert).count() == 2 and s.query(SafetyViolation).count() == 1
    s.close()


# --------------------------------------------------------------- safety
def test_zone_uses_feet_position():
    from app.core.safety_engine import SafetyEngine
    zone = np.array([[0, 100], [200, 100], [200, 200], [0, 200]], np.int32)
    inside = {"bbox": (50, 20, 80, 150), "zone_intrusion": False}    # feet at y=150
    outside = {"bbox": (50, 0, 80, 90), "zone_intrusion": False}     # feet at y=90
    SafetyEngine._check_zones([inside, outside], [zone])
    assert inside["zone_intrusion"] and not outside["zone_intrusion"]


@pytest.mark.parametrize("name,bad", [("no_helmet", True), ("NO-Hardhat", True),
                                      ("NO-Safety Vest", True), ("helmet", False),
                                      ("person", False)])
def test_violation_class_names(name, bad):
    from app.core.safety_engine import is_violation_class
    assert is_violation_class(name) is bad


def _fake_yolo_onnx(path, rows):
    """An ONNX model that ignores its input and always outputs `rows`."""
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper, numpy_helper
    const = numpy_helper.from_array(rows.astype(np.float32), "dets")
    nodes = [
        helper.make_node("ReduceMean", ["images"], ["m"], keepdims=0),
        helper.make_node("Mul", ["m", "zero"], ["z"]),
        helper.make_node("Add", ["dets", "z"], ["output0"]),
    ]
    graph = helper.make_graph(
        nodes, "fake", [helper.make_tensor_value_info("images", TensorProto.FLOAT,
                                                      [1, 3, 640, 640])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT, list(rows.shape))],
        initializer=[const, numpy_helper.from_array(np.zeros((), np.float32), "zero")])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, path)


def test_onnx_yolov8_output_is_parsed(tmp_path):
    from app.core.safety_engine import SafetyEngine
    names = tmp_path / "ppe.names"
    names.write_text("helmet\nno_helmet\n")
    n = 50
    out = np.zeros((1, 6, n), np.float32)          # YOLOv8: (1, 4 + classes, N)
    out[0, :4, 0] = [320, 320, 100, 200]           # centre box, 640 input space
    out[0, 5, 0] = 0.9                             # class 1 = no_helmet
    out[0, :4, 1] = [322, 321, 100, 200]           # duplicate -> removed by NMS
    out[0, 5, 1] = 0.8
    model = tmp_path / "fake.onnx"
    _fake_yolo_onnx(str(model), out)
    eng = SafetyEngine()
    assert eng.load_model(str(model), str(names)) == ""
    dets = eng.detect(np.zeros((480, 1280, 3), np.uint8))
    assert len(dets) == 1
    d = dets[0]
    assert d["class"] == "no_helmet" and d["violation"]
    x1, y1, x2, y2 = d["bbox"]
    assert abs((x1 + x2) / 2 - 640) <= 2 and abs((y1 + y2) / 2 - 240) <= 2


def test_pt_file_gives_helpful_error(tmp_path):
    from app.core.safety_engine import SafetyEngine
    names = tmp_path / "n.names"
    names.write_text("a\n")
    assert "ONNX" in SafetyEngine().load_model(str(tmp_path / "best.pt"), str(names))


# ------------------------------------------------------------------ faces
def test_legacy_single_encoding_is_still_read():
    import pickle
    from app.core.face_engine import decode_encodings, encode_encodings
    vec = np.random.rand(128)
    assert len(decode_encodings(pickle.dumps(vec))) == 1
    assert len(decode_encodings(pickle.dumps(np.random.rand(16384)))) == 0  # old Haar data
    assert len(decode_encodings(encode_encodings([vec, vec]))) == 2
