"""AI worker, face recognition and real RTSP streaming (served by ffmpeg)."""
import os
import shutil
import socket
import subprocess
import time

import cv2
import numpy as np
import pytest

from app.config import Config
from tests.conftest import needs_face


def _enroll(db, fe, image, name="Astro"):
    from app.core.face_engine import FaceEngine
    from app.models import Person
    enc, msg = fe.compute_encoding(image)
    assert enc is not None, msg
    s = db.get_session()
    p = Person(name=name, face_encoding=FaceEngine.add_encoding(None, enc))
    s.add(p)
    s.commit()
    s.close()
    fe.load_known_faces([{"id": p.id, "name": name, "face_encoding": p.face_encoding}])
    return p.id


@needs_face
def test_enroll_and_recognise(db, face_image):
    from app.core.face_engine import FaceEngine
    fe = FaceEngine("/tmp")
    pid = _enroll(db, fe, face_image)
    shifted = np.roll(face_image, 15, axis=1)  # same person, slightly different frame
    faces = fe.recognize_faces(shifted, 0.5, scale=1.0)
    assert faces and faces[0]["person_id"] == pid
    fe.load_known_faces([])
    assert fe.recognize_faces(face_image, 0.5, scale=1.0)[0]["person_id"] is None


@needs_face
def test_enroll_rejects_photo_without_face():
    from app.core.face_engine import FaceEngine
    fe = FaceEngine("/tmp")
    enc, msg = fe.compute_encoding(np.zeros((300, 300, 3), np.uint8))
    assert enc is None and "No face" in msg


def _wait(qtbot, predicate, timeout=20000):
    qtbot.waitUntil(lambda: bool(predicate()), timeout=timeout)


@needs_face
def test_worker_records_attendance_only_when_switch_is_on(qtbot, db, face_image):
    from app.core.ai_worker import AIWorker
    from app.core.face_engine import FaceEngine
    from app.models import AttendanceRecord
    fe = FaceEngine("/tmp")
    _enroll(db, fe, face_image)
    worker = AIWorker(db, fe)
    events = []
    worker.attendance_event.connect(events.append)
    worker.set_cameras([{"id": 1, "name": "Gate", "attendance_tracking": False}])
    worker.start()
    try:
        worker.submit_frame(1, face_image)
        qtbot.wait(2500)
        assert events == []                               # switch off: nothing
        worker.set_cameras([{"id": 1, "name": "Gate", "attendance_tracking": True}])
        worker.submit_frame(1, face_image.copy())
        _wait(qtbot, lambda: events)
        assert events[0]["type"] == "check_in" and events[0]["camera_name"] == "Gate"
        s = db.get_session()
        assert s.query(AttendanceRecord).count() == 1
        s.close()
    finally:
        worker.stop()


def test_worker_safety_zone_alert_and_monitoring(qtbot, db):
    from app.core.ai_worker import AIWorker
    from app.core.face_engine import FaceEngine
    from app.models import Alert, SafetyViolation
    worker = AIWorker(db, FaceEngine("/tmp"))
    alerts, frames = [], []
    worker.alert_created.connect(alerts.append)
    worker.annotated_frame.connect(lambda kind, cid, img: frames.append((kind, cid, img.size())))
    worker.set_cameras([{"id": 7, "name": "Workshop", "safety_watch": False}])
    worker.set_zones(7, [[[0, 0], [640, 0], [640, 480], [0, 480]]])
    worker.start()
    try:
        _wait(qtbot, lambda: worker.safety_engine is not None)
        worker.safety_engine.detect = lambda frame, zones: [
            {"bbox": (100, 100, 200, 400), "class": "person", "confidence": 0.9,
             "violation": False, "zone_intrusion": True}]
        worker.set_monitor("safety", 7)          # page opened -> runs even if switch off
        for _ in range(5):
            worker.submit_frame(7, np.zeros((480, 640, 3), np.uint8))
            qtbot.wait(300)
        _wait(qtbot, lambda: alerts and frames)
        assert len(alerts) == 1                   # cooldown: one alert, not one per frame
        assert alerts[0]["severity"] == "high" and alerts[0]["type"] == "Zone intrusion"
        assert frames[-1][0] == "safety" and frames[-1][2].width() == 640
        s = db.get_session()
        assert s.query(Alert).count() == 1 and s.query(SafetyViolation).count() == 1
        s.close()
    finally:
        worker.stop()


def test_async_task_delivers_on_ui_thread(qtbot):
    import threading
    from app.ui.async_task import run_async
    seen = {}

    def work():
        seen["worker"] = threading.get_ident()
        return 42

    run_async(work, lambda res, err: seen.update(res=res, cb=threading.get_ident()))
    _wait(qtbot, lambda: "res" in seen, 5000)
    assert seen["res"] == 42 and seen["cb"] == threading.get_ident() != seen["worker"]


# ------------------------------------------------------- real RTSP stream
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _gst_python():
    """A Python interpreter that has GStreamer's RTSP server bindings."""
    candidates = [os.environ.get("CLASSGUARD_GST_PYTHON"), "/usr/bin/python3",
                  "/usr/bin/python3.12", shutil.which("python3")]
    for exe in filter(None, candidates):
        ok = subprocess.run([exe, "-c", "import gi; gi.require_version('GstRtspServer',"
                             "'1.0'); from gi.repository import GstRtspServer"],
                            capture_output=True).returncode == 0
        if ok:
            return exe
    return None


@pytest.fixture
def rtsp_camera(tmp_path, face_image):
    """Serve a face photo as a live H.264 RTSP stream, like an IP camera."""
    exe = _gst_python()
    if not exe:
        pytest.skip("GStreamer RTSP server (python3-gi, gir1.2-gst-rtsp-server-1.0) not installed")
    img = tmp_path / "face.png"
    cv2.imwrite(str(img), cv2.resize(face_image, (640, 324)))
    port = _free_port()
    url = f"rtsp://127.0.0.1:{port}/live"
    script = os.path.join(os.path.dirname(__file__), "rtsp_test_server.py")
    procs = []

    def start():
        p = subprocess.Popen([exe, script, str(port), str(img)],
                             stdout=subprocess.PIPE, text=True)
        procs.append(p)
        assert p.stdout.readline().strip() == "READY"
        return p

    yield url, start
    for p in procs:
        p.kill()
        p.wait()


@needs_face
def test_rtsp_stream_attendance_and_reconnect(qtbot, db, face_image, rtsp_camera):
    from app.core.ai_worker import AIWorker
    from app.core.face_engine import FaceEngine
    from app.core.stream_manager import StreamManager
    url, start_server = rtsp_camera
    fe = FaceEngine("/tmp")
    _enroll(db, fe, face_image)
    server = start_server()

    sm = StreamManager()
    sm.reconnect_delay = 1
    worker = AIWorker(db, fe)
    sm.add_frame_listener(worker.submit_frame)
    worker.set_cameras([{"id": 3, "name": "Classroom", "attendance_tracking": True}])
    statuses, frames, events = [], [], []
    worker.attendance_event.connect(events.append)
    worker.start()
    try:
        sm.start_stream(3, url, on_frame=lambda cid, img: frames.append(img.size()),
                        on_status=lambda cid, ok, msg: statuses.append(ok))
        _wait(qtbot, lambda: frames and events, 30000)
        assert statuses[0] is True and events[0]["type"] == "check_in"

        server.kill()                                 # camera unplugged
        server.wait()
        _wait(qtbot, lambda: statuses[-1] is False, 20000)
        n = len(frames)
        start_server()                                # camera back
        _wait(qtbot, lambda: statuses[-1] is True and len(frames) > n + 5, 40000)
    finally:
        sm.stop_all(wait_ms=5000)
        worker.stop()


def test_unreachable_camera_reports_failure_quickly(qtbot):
    from app.core.stream_manager import StreamManager
    port = _free_port()  # nothing listening
    sm = StreamManager()
    statuses = []
    t0 = time.monotonic()
    sm.start_stream(9, f"rtsp://127.0.0.1:{port}/x",
                    on_status=lambda cid, ok, msg: statuses.append((ok, msg)))
    try:
        _wait(qtbot, lambda: statuses, 15000)
        assert statuses[0][0] is False and time.monotonic() - t0 < 15
    finally:
        sm.stop_all(wait_ms=5000)


def test_rtsp_stream_is_recorded_and_playable(qtbot, db, rtsp_camera, monkeypatch, tmp_path):
    from app.core.recording import RecordingManager
    from app.core.stream_manager import StreamManager
    from app.models import Recording
    from app.ui.widgets.video_player import VideoPlayer
    monkeypatch.setattr(Config, "RECORDINGS_DIR", str(tmp_path / "rec"))
    url, start_server = rtsp_camera
    start_server()
    sm = StreamManager()
    rec = RecordingManager(db)
    rec.set_cameras([{"id": 5, "recording_mode": "continuous"}])
    sm.add_frame_listener(rec.on_frame)
    sm.start_stream(5, url)
    try:
        _wait(qtbot, lambda: rec.is_recording(5), 20000)
        qtbot.wait(3000)
    finally:
        sm.stop_all(wait_ms=5000)
        rec.stop_all()
    s = db.get_session()
    row = s.query(Recording).filter(Recording.end_time.isnot(None)).one()
    s.close()
    assert (row.end_time - row.start_time).total_seconds() >= 2
    player = VideoPlayer()
    qtbot.addWidget(player)
    assert player.open(row.path, row.start_time, autoplay=False)
    assert player._frames >= 15 and player._last_frame.shape[1] == 640
