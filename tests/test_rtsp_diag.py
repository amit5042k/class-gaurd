"""RTSP diagnosis against a mock NVR (Digest login, some channels empty)."""
import hashlib
import re
import socket
import threading

import pytest

from app.protocols import rtsp_diag
from app.protocols.rtsp_diag import AUTH, BUSY, NOT_FOUND, OK, UNREACHABLE, describe

REALM, NONCE = "IP Camera(12345)", "abc123nonce"


class MockNVR:
    """Answers DESCRIBE like a Dahua-style NVR: Digest auth, video only on some channels."""

    def __init__(self, user="admin", pwd="Secret@12", live=(1, 5), busy=(), silent=()):
        self.user, self.pwd, self.live, self.busy = user, pwd, set(live), set(busy)
        self.silent = set(silent)  # channels that accept PLAY but send no video
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        with conn:
            buf = b""
            while True:
                data = conn.recv(4096)
                if not data:
                    return
                buf += data
                while b"\r\n\r\n" in buf:
                    req, buf = buf.split(b"\r\n\r\n", 1)
                    conn.sendall(self._answer(req.decode()).encode())
                    if req.startswith(b"PLAY"):
                        ch = int(re.search(rb"channel=(\d+)", req).group(1))
                        if ch not in self.silent:
                            for _ in range(50):   # interleaved RTP on channel 0
                                conn.sendall(b"$\x00\x00\x0c" + b"\x80" * 12)

    def _answer(self, req: str) -> str:
        first = req.split("\r\n")[0]
        method, url = first.split(" ")[0], first.split(" ")[1]
        cseq = re.search(r"CSeq: (\d+)", req).group(1)
        auth = re.search(r"Authorization: (.*)", req)
        if not auth or not self._valid(auth.group(1), url, method):
            return (f"RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\n"
                    f'WWW-Authenticate: Digest realm="{REALM}", nonce="{NONCE}"\r\n\r\n')
        ch = int(re.search(r"channel=(\d+)", url).group(1))
        if ch in self.busy:
            return f"RTSP/1.0 453 Not Enough Bandwidth\r\nCSeq: {cseq}\r\n\r\n"
        if ch not in self.live:
            return f"RTSP/1.0 404 Not Found\r\nCSeq: {cseq}\r\n\r\n"
        if method == "DESCRIBE":
            sdp = "v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\na=control:trackID=1\r\n"
            return (f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nContent-Base: {url}/\r\n"
                    f"Content-Type: application/sdp\r\nContent-Length: {len(sdp)}\r\n\r\n{sdp}")
        extra = "Session: 12345;timeout=60\r\n" if method in ("SETUP", "PLAY") else ""
        return f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\n{extra}Content-Length: 0\r\n\r\n"

    def _valid(self, header: str, url: str, method: str = "DESCRIBE") -> bool:
        p = dict(re.findall(r'(\w+)="?([^",]*)"?', header))
        ha1 = hashlib.md5(f"{self.user}:{REALM}:{self.pwd}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{url}".encode()).hexdigest()
        expected = hashlib.md5(f"{ha1}:{NONCE}:{ha2}".encode()).hexdigest()
        return p.get("username") == self.user and p.get("response") == expected

    def url(self, ch, user="admin", pwd="Secret%4012"):
        return (f"rtsp://{user}:{pwd}@127.0.0.1:{self.port}"
                f"/cam/realmonitor?channel={ch}&subtype=1")

    def close(self):
        self.sock.close()


@pytest.fixture
def nvr():
    m = MockNVR(busy={7})
    yield m
    m.close()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_digest_login_and_channel_states(nvr):
    assert describe(nvr.url(1))[0] == OK                      # escaped '@' in password works
    assert describe(nvr.url(2))[0] == NOT_FOUND
    assert describe(nvr.url(7))[0] == BUSY
    kind, msg = describe(nvr.url(1, pwd="wrong"))
    assert kind == AUTH and "password" in msg


def test_unreachable_device():
    kind, msg = describe(f"rtsp://u:p@127.0.0.1:{_free_port()}/x", timeout=2)
    assert kind == UNREACHABLE and "Can't reach 127.0.0.1" in msg


def test_explain_failure_messages(nvr):
    assert "no camera is connected" in rtsp_diag.explain_failure(nvr.url(3))
    assert "couldn't be decoded" in rtsp_diag.explain_failure(nvr.url(5))


def test_nvr_probe_and_import_only_live_channels(db, nvr):
    from app.core.camera_manager import CameraManager
    mgr = CameraManager(db)
    rec = mgr.add_nvr({"name": "NVR1", "brand": "Dahua", "host": "127.0.0.1",
                       "username": "admin", "password": "Secret@12", "total_channels": 8})
    mgr.channel_url = lambda n, ch, rtsp_port=554: nvr.url(ch)   # point at the mock
    probe = mgr.probe_nvr_channels(rec.id)
    live = {ch for ch, (kind, _) in probe.items() if kind == OK}
    assert live == {1, 5} and probe[7][0] == BUSY and probe[2][0] == NOT_FOUND
    assert mgr.import_nvr_channels(rec.id, {1: "Gate"}, only=live) == 2
    names = sorted(c["name"] for c in mgr.get_all_cameras())
    assert names == ["NVR1 - Channel 5", "NVR1 - Gate"]


def test_stream_tile_shows_the_reason(qtbot, nvr):
    from app.core.stream_manager import StreamManager
    sm = StreamManager()
    statuses = []
    sm.start_stream(1, nvr.url(4), on_status=lambda cid, ok, msg: statuses.append(msg))
    try:
        qtbot.waitUntil(lambda: any("no camera" in m for m in statuses), timeout=30000)
        assert statuses[0].startswith("Connecting")
    finally:
        sm.stop_all(wait_ms=30000)


def test_natural_camera_order(db):
    from app.core.camera_manager import CameraManager
    mgr = CameraManager(db)
    for n in (10, 2, 1, 11):
        mgr.add_camera({"name": f"NVR1 - Channel {n}", "brand": "Dahua", "host": "h"})
    assert [c["name"][-2:].strip() for c in mgr.get_all_cameras()] == ["1", "2", "10", "11"]


def test_live_view_keeps_status_across_layout_change(qtbot):
    from app.core.stream_manager import StreamManager
    from app.ui.pages.live_view import LiveView
    sm = StreamManager()
    lv = LiveView(None, sm)
    qtbot.addWidget(lv)
    lv.load_cameras([{"id": 1, "name": "Cam 1", "rtsp_url": f"rtsp://127.0.0.1:{_free_port()}/x"}])
    lv.start_all_streams()
    try:
        qtbot.waitUntil(lambda: "Can't reach" in lv._grid.get_cell(1)._canvas.text(),
                        timeout=30000)
        lv._layout_cb.setCurrentIndex(3)          # 4x4: tiles are rebuilt
        assert "Can't reach" in lv._grid.get_cell(1)._canvas.text()
    finally:
        sm.stop_all(wait_ms=30000)


def test_diagnosis_report_for_empty_channel_hides_password(nvr):
    from app.core.diagnose import run_diagnosis
    report = run_diagnosis({"name": "NVR1 - Channel 3", "brand": "Dahua", "channel": 3,
                            "rtsp_url": nvr.url(3), "password": "Secret@12"})
    assert "no camera is connected" in report and "login digest" in report
    assert "Secret" not in report and "****" in report


def test_live_view_streams_only_visible_page(qtbot):
    from app.core.stream_manager import StreamManager
    from app.ui.pages.live_view import LiveView
    sm = StreamManager()
    lv = LiveView(None, sm)
    qtbot.addWidget(lv)
    port = _free_port()
    cams = [{"id": i, "name": f"Ch {i}", "rtsp_url": f"rtsp://127.0.0.1:{port}/{i}"}
            for i in range(1, 9)]
    lv.load_cameras(cams)                       # 2x2: page 1 = cameras 1-4
    lv.set_pinned({8})                          # e.g. camera 8 records 24x7
    sm.start_stream(8, cams[7]["rtsp_url"])
    try:
        lv.start_all_streams()
        qtbot.waitUntil(lambda: all(sm.is_streaming(i) for i in (1, 2, 3, 4)), timeout=10000)
        assert not any(sm.is_streaming(i) for i in (5, 6, 7))
        lv._go_page(1)                           # page 2 = cameras 5-8
        qtbot.waitUntil(lambda: all(sm.is_streaming(i) for i in (5, 6, 7)), timeout=10000)
        assert not any(sm.is_streaming(i) for i in (1, 2, 3, 4))
        lv.stop_all_streams()
        assert sm.is_streaming(8)                # pinned camera keeps going
    finally:
        sm.stop_all(wait_ms=30000)               # don't leave reconnect loops running


def test_slow_udp_open_does_not_block_other_cameras():
    """A UDP attempt to a dead device must not hold up TCP opens of others."""
    import threading
    import time
    from app.core import stream_manager as smod
    real = smod.cv2.VideoCapture

    class Dummy:
        def release(self):
            pass

    def slow(source, *a, **k):
        if "slow" in str(source):
            time.sleep(3)          # like a 10 s timeout on an unreachable camera
        return Dummy()

    smod.cv2.VideoCapture = slow
    try:
        t = threading.Thread(target=smod.open_capture, args=("rtsp://h/slow", "udp"))
        t.start()
        time.sleep(0.1)
        t0 = time.monotonic()
        smod.open_capture("rtsp://h/fast", "tcp").release()
        assert time.monotonic() - t0 < 1.0
        t.join()
    finally:
        smod.cv2.VideoCapture = real


def test_media_probe_counts_packets_and_detects_silence():
    from app.protocols.rtsp_diag import media_probe
    m = MockNVR(silent={5})
    try:
        live = media_probe(m.url(1), seconds=1)
        assert live["packets"] == 50 and not live["error"], live
        silent = media_probe(m.url(5), seconds=1)
        assert silent["packets"] == 0 and not silent["error"], silent
    finally:
        m.close()


def test_report_says_no_video_data_for_silent_channel():
    from app.core.diagnose import run_diagnosis
    m = MockNVR(silent={5})
    try:
        report = run_diagnosis({"name": "NVR1 - Channel 5", "brand": "Hikvision", "channel": 5,
                                "rtsp_url": m.url(5), "password": "Secret@12"},
                               stream_seconds=1)
        assert "Video data (no decoder, TCP): NONE" in report
        assert "sends NO video data" in report and "Secret" not in report
    finally:
        m.close()


def test_tile_says_device_sends_nothing(qtbot):
    from app.core.stream_manager import StreamManager
    m = MockNVR(silent={5})
    sm = StreamManager()
    statuses = []
    sm.start_stream(5, m.url(5), on_status=lambda cid, ok, msg: statuses.append(msg))
    try:
        qtbot.waitUntil(lambda: any("sends no video data" in s for s in statuses),
                        timeout=60000)
    finally:
        sm.stop_all(wait_ms=30000)
        m.close()


def _grey_with_clock():
    import numpy as np
    f = np.full((360, 640, 3), 128, np.uint8)
    f[10:30, 20:120] = (20, 200, 20)     # only the on-screen clock is drawn
    return f


def test_placeholder_frame_detection():
    import numpy as np
    from app.core.stream_manager import is_placeholder_frame
    assert is_placeholder_frame(_grey_with_clock())
    real = np.random.default_rng(1).integers(0, 255, (360, 640, 3), dtype=np.uint8)
    assert not is_placeholder_frame(real)
    assert not is_placeholder_frame(np.zeros((360, 640, 3), np.uint8))     # night scene


def test_grey_frames_are_held_back_and_keyframe_requested(qtbot, monkeypatch):
    import numpy as np
    from app.core import stream_manager as smod
    real = np.full((360, 640, 3), (30, 90, 160), np.uint8)
    frames = [_grey_with_clock()] * 30 + [real] * 30

    class Fake:
        def isOpened(self):
            return True

        def read(self):
            import time
            time.sleep(0.01)
            return (True, frames.pop(0)) if frames else (False, None)

        def release(self):
            pass

    monkeypatch.setattr(smod, "_open_guarded", lambda *a, **k: Fake())
    monkeypatch.setattr(smod.StreamThread, "_precheck", lambda self: ("", ""))
    requests, statuses, shown = [], [], []
    sm = smod.StreamManager()
    sm.start_stream(1, "rtsp://cam/x", keyframe_request=lambda: requests.append(1),
                    on_status=lambda c, ok, m: statuses.append((ok, m)),
                    on_frame=lambda c, img: shown.append(img.pixel(300, 200)))
    try:
        qtbot.waitUntil(lambda: bool(shown), timeout=10000)
        assert any(m == smod.WAITING_KEYFRAME for _, m in statuses)
        assert requests                                  # keyframe asked for
        from PyQt6.QtGui import QColor
        assert QColor(shown[0]).red() == 160             # first shown frame is the real one
    finally:
        sm.stop_all(wait_ms=10000)


def test_hikvision_keyframe_request_paths():
    from app.protocols.hikvision import HikvisionAPI
    from tests.test_live_and_playback import FakeResponse, FakeSession
    api = HikvisionAPI("10.0.5.1", 80, "admin", "pw")
    api.session = FakeSession({"/Streaming/channels/102/requestKeyFrame": FakeResponse(404)})
    assert api.request_keyframe(102)
    urls = [u for _, u, _ in api.session.calls]
    assert urls[0].endswith("/ISAPI/Streaming/channels/102/requestKeyFrame")
    assert urls[1].endswith("/ISAPI/ContentMgmt/StreamingProxy/channels/102/requestKeyFrame")


def test_keyframe_detection_in_rtp_payloads():
    from app.protocols.rtsp_diag import _is_keyframe, _nal_types, _rtp_payload
    rtp = lambda payload: bytes([0x80, 96]) + b"\x00\x01" + b"\x00" * 8 + payload  # noqa: E731
    # H.264: FU-A start of an IDR slice, FU-A middle part, STAP-A with SPS+PPS
    assert _nal_types(_rtp_payload(rtp(b"\x7c\x85abc")), "H264") == [5]
    assert _nal_types(_rtp_payload(rtp(b"\x7c\x05abc")), "H264") == []
    stap = b"\x18" + b"\x00\x02\x67\x42" + b"\x00\x02\x68\xce"
    assert _nal_types(_rtp_payload(rtp(stap)), "H264") == [7, 8]
    assert _is_keyframe(5, "H264") and not _is_keyframe(1, "H264")
    # H.265: FU start of an IDR_W_RADL (19) picture, and a plain P slice (1)
    assert _nal_types(_rtp_payload(rtp(b"\x62\x01\x93xyz")), "H265") == [19]
    assert _is_keyframe(19, "H265") and not _is_keyframe(1, "H265")


def test_still_pictures_replace_endless_grey_video(qtbot, monkeypatch):
    import numpy as np
    from app.core import stream_manager as smod

    class Grey:
        def isOpened(self):
            return True

        def read(self):
            import time
            time.sleep(0.01)
            return True, _grey_with_clock()

        def release(self):
            pass

    monkeypatch.setattr(smod, "_open_guarded", lambda *a, **k: Grey())
    monkeypatch.setattr(smod.StreamThread, "_precheck", lambda self: ("", ""))
    monkeypatch.setattr(smod, "GREY_WARN_S", 0.3)
    still = np.full((360, 640, 3), (30, 90, 160), np.uint8)
    statuses, shown = [], []
    sm = smod.StreamManager()
    sm.start_stream(1, "rtsp://cam/x", snapshot=lambda: still,
                    on_status=lambda c, ok, m: statuses.append((ok, m)),
                    on_frame=lambda c, img: shown.append(img.pixel(300, 200)))
    try:
        qtbot.waitUntil(lambda: bool(shown), timeout=10000)
        from PyQt6.QtGui import QColor
        assert QColor(shown[0]).red() == 160               # the still, not grey video
        assert (True, smod.SNAPSHOT_MSG) in statuses
    finally:
        sm.stop_all(wait_ms=10000)


def test_hikvision_snapshot_falls_back_to_proxy_path():
    from app.protocols.hikvision import HikvisionAPI
    from tests.test_live_and_playback import FakeResponse, FakeSession
    jpeg = b"\xff\xd8\xff\xe0rest"
    api = HikvisionAPI("10.0.5.1", 80, "admin", "pw")
    api.session = FakeSession({"/ISAPI/Streaming/channels/102/picture": FakeResponse(403),
                               "StreamingProxy/channels/102/picture":
                                   FakeResponse(200, content=jpeg)})
    assert api.snapshot(102) == jpeg
    api.session.calls.clear()
    assert api.snapshot(102) == jpeg                       # remembers the working path
    assert "StreamingProxy" in api.session.calls[0][1]
