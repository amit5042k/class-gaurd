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

    def __init__(self, user="admin", pwd="Secret@12", live=(1, 5), busy=()):
        self.user, self.pwd, self.live, self.busy = user, pwd, set(live), set(busy)
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

    def _answer(self, req: str) -> str:
        first = req.split("\r\n")[0]
        url = first.split(" ")[1]
        cseq = re.search(r"CSeq: (\d+)", req).group(1)
        auth = re.search(r"Authorization: (.*)", req)
        if not auth or not self._valid(auth.group(1), url):
            return (f"RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\n"
                    f'WWW-Authenticate: Digest realm="{REALM}", nonce="{NONCE}"\r\n\r\n')
        ch = int(re.search(r"channel=(\d+)", url).group(1))
        if ch in self.busy:
            return f"RTSP/1.0 453 Not Enough Bandwidth\r\nCSeq: {cseq}\r\n\r\n"
        if ch not in self.live:
            return f"RTSP/1.0 404 Not Found\r\nCSeq: {cseq}\r\n\r\n"
        return f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nContent-Length: 0\r\n\r\n"

    def _valid(self, header: str, url: str) -> bool:
        p = dict(re.findall(r'(\w+)="?([^",]*)"?', header))
        ha1 = hashlib.md5(f"{self.user}:{REALM}:{self.pwd}".encode()).hexdigest()
        ha2 = hashlib.md5(f"DESCRIBE:{url}".encode()).hexdigest()
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
        sm.stop_all(wait_ms=5000)


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
        sm.stop_all(wait_ms=5000)


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
        sm.stop_all(wait_ms=5000)
