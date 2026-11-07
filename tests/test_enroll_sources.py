"""Enrolling faces without a webcam: from a CCTV camera."""
import shutil
import socket
import subprocess
import time

import cv2
import numpy as np
import pytest

from tests.conftest import needs_face


def test_no_webcam_message(qtbot, monkeypatch):
    from app.core import camera_feed
    monkeypatch.setattr(camera_feed, "open_webcam", lambda: (None, ""))
    feed = camera_feed.CameraFeed({"kind": "webcam"})
    failed = []
    feed.failed.connect(failed.append)
    feed.start()
    qtbot.waitUntil(lambda: bool(failed), timeout=5000)
    feed.wait(2000)
    assert "Choose a CCTV camera" in failed[0]


def test_webcam_tries_every_driver_and_index(monkeypatch):
    from app.core import camera_feed
    tried = []

    class Cap:
        def __init__(self, index, backend):
            tried.append((index, backend))
            self.ok = (index, backend) == (1, camera_feed.webcam_backends()[-1])

        def isOpened(self):
            return self.ok

        def read(self):
            return True, np.zeros((4, 4, 3), np.uint8)

        def release(self):
            pass
    monkeypatch.setattr(camera_feed.cv2, "VideoCapture", Cap)
    cap, desc = camera_feed.open_webcam()
    assert desc == "webcam 1" and len(tried) == len(camera_feed.webcam_backends()) * 2


def test_largest_face_crop_and_main_stream_url():
    from app.core.camera_feed import enrollment_url, largest_face_crop
    frame = np.zeros((1000, 1600, 3), np.uint8)
    crop = largest_face_crop(frame, [(100, 100, 140, 140), (800, 300, 900, 400)])
    assert crop.shape[:2] == (600, 600)            # 100 px face + room, enlarged ×2
    cam = {"brand": "Hikvision", "host": "10.0.5.1", "port": 554, "username": "admin",
           "password": "p", "channel": 3, "rtsp_url": "rtsp://x/Streaming/Channels/302"}
    assert enrollment_url(cam).endswith("/Streaming/Channels/301")      # main stream
    assert enrollment_url({**cam, "custom_rtsp_url": "rtsp://c"}) == cam["rtsp_url"]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@needs_face
def test_enroll_from_cctv_camera(qtbot, db, tmp_path, face_image):
    from app.core.face_engine import FaceEngine, decode_encodings
    from app.models import Person
    from app.ui.pages.face_enrollment import FaceEnrollment
    if not shutil.which("/usr/bin/python3.12"):
        pytest.skip("needs the GStreamer test server")
    img = str(tmp_path / "face.png")
    big = cv2.copyMakeBorder(face_image, 60, 60, 200, 200, cv2.BORDER_CONSTANT, value=(90, 90, 90))
    cv2.imwrite(img, big)
    port = _free_port()
    srv = subprocess.Popen(["/usr/bin/python3.12", "tests/rtsp_test_server.py", str(port), img,
                            "h264", "10"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert b"READY" in srv.stdout.readline()          # wait until it serves
    time.sleep(0.5)
    s = db.get_session()
    p = Person(name="Asha")
    s.add(p)
    s.commit()
    pid = p.id
    s.close()
    page = FaceEnrollment(db, FaceEngine(str(tmp_path / "faces")))
    qtbot.addWidget(page)
    try:
        page.set_cameras([{"id": 7, "name": "Gate", "brand": "Generic RTSP",
                           "rtsp_url": f"rtsp://127.0.0.1:{port}/live",
                           "custom_rtsp_url": f"rtsp://127.0.0.1:{port}/live"}])
        assert page._source.count() == 2
        page._source.setCurrentIndex(1)
        page._selected_pid = pid
        page._toggle_camera()
        qtbot.waitUntil(lambda: page._btn_snap.isEnabled(), timeout=20000)
        qtbot.waitUntil(lambda: bool(page._feed.boxes), timeout=10000)   # face outlined
        page._capture_enroll()
        qtbot.waitUntil(lambda: "enrolled" in page._status.text()
                        or "No face" in page._status.text(), timeout=30000)
        assert "1 photo" in page._status.text(), page._status.text()
        s = db.get_session()
        assert len(decode_encodings(s.get(Person, pid).face_encoding)) == 1
        s.close()
    finally:
        page._stop_camera()
        srv.terminate()
        srv.wait()
