"""Playback of recordings stored on the NVR."""
import re
import shutil
import socket
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

import pytest

HIK = {"id": 1, "name": "NVR1 - Channel 3", "brand": "Hikvision", "host": "10.0.5.1",
       "port": 554, "http_port": 80, "username": "admin", "password": "Pa@ss", "channel": 3}


def test_playback_urls_per_brand():
    from app.protocols.nvr_playback import playback_url, supports_playback
    t = datetime(2026, 11, 6, 9, 30, 5)
    end = datetime(2026, 11, 6, 10, 0, 0)
    assert playback_url(HIK, t, end) == (
        "rtsp://admin:Pa%40ss@10.0.5.1:554/Streaming/tracks/301/"
        "?starttime=20261106T093005Z&endtime=20261106T100000Z")
    dahua = dict(HIK, brand="CP Plus", channel=2)
    assert playback_url(dahua, t, end) == (
        "rtsp://admin:Pa%40ss@10.0.5.1:554/cam/playback?channel=2&subtype=0"
        "&starttime=2026_11_06_09_30_05&endtime=2026_11_06_10_00_00")
    unv = dict(HIK, brand="UNV", channel=1)
    assert playback_url(unv, t, end).endswith(
        f"/c1/b{int(t.timestamp())}/e{int(end.timestamp())}/replay/")
    assert playback_url(HIK, t).endswith("endtime=20261106T235959Z")      # to end of day
    assert not supports_playback(dict(HIK, brand="ONVIF"))
    assert not supports_playback(dict(HIK, custom_rtsp_url="rtsp://x/y"))


class _Resp:
    def __init__(self, status=200, text=""):
        self.status_code, self.text, self.content = status, text, text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


HIK_PAGE1 = """<?xml version="1.0" encoding="UTF-8"?>
<CMSearchResult xmlns="http://www.hikvision.com/ver20/XMLSchema">
<responseStatusStrg>MORE</responseStatusStrg>
<matchList>
<searchMatchItem><timeSpan><startTime>2026-11-06T08:00:00Z</startTime>
<endTime>2026-11-06T09:00:00Z</endTime></timeSpan>
<mediaSegmentDescriptor><metadataDescriptor>recordType.meta.std-cgi.com/CMR
</metadataDescriptor></mediaSegmentDescriptor></searchMatchItem>
<searchMatchItem><timeSpan><startTime>2026-11-06T09:00:00Z</startTime>
<endTime>2026-11-06T10:00:00Z</endTime></timeSpan></searchMatchItem>
</matchList></CMSearchResult>"""
HIK_PAGE2 = """<?xml version="1.0" encoding="UTF-8"?>
<CMSearchResult xmlns="http://www.hikvision.com/ver20/XMLSchema">
<responseStatusStrg>OK</responseStatusStrg>
<matchList><searchMatchItem><timeSpan><startTime>2026-11-06T12:00:00Z</startTime>
<endTime>2026-11-06T12:05:00Z</endTime></timeSpan>
<mediaSegmentDescriptor><metadataDescriptor>recordType.meta.std-cgi.com/VMD
</metadataDescriptor></mediaSegmentDescriptor></searchMatchItem></matchList>
</CMSearchResult>"""


def test_hikvision_search_pages_and_merges(monkeypatch):
    from app.protocols import nvr_playback as nvr
    bodies = []

    class S:
        def post(self, url, data=None, **kw):
            bodies.append((url, data))
            return _Resp(200, HIK_PAGE1 if len(bodies) == 1 else HIK_PAGE2)
    monkeypatch.setattr(nvr, "_session", lambda cam: S())
    segs = nvr.search_recordings(HIK, date(2026, 11, 6))
    assert bodies[0][0] == "http://10.0.5.1:80/ISAPI/ContentMgmt/search"
    assert "<trackID>301</trackID>" in bodies[0][1]
    assert "<searchResultPostion>2</searchResultPostion>" in bodies[1][1]
    assert [(s["start"].hour, s["end"].hour, s["kind"]) for s in segs] == [
        (8, 10, "continuous"), (12, 12, "motion")]


def test_hikvision_search_wrong_password(monkeypatch):
    from app.protocols import nvr_playback as nvr

    class S:
        def post(self, *a, **k):
            return _Resp(401)
    monkeypatch.setattr(nvr, "_session", lambda cam: S())
    with pytest.raises(PermissionError):
        nvr.search_recordings(HIK, date(2026, 11, 6))


def test_dahua_search(monkeypatch):
    from app.protocols import nvr_playback as nvr
    calls = []
    found = ("found=2\r\nitems[0].Channel=0\r\nitems[0].StartTime=2026-11-06 07:00:00\r\n"
             "items[0].EndTime=2026-11-06 08:00:00\r\nitems[0].Type=dav\r\n"
             "items[1].StartTime=2026-11-06 13:00:00\r\nitems[1].EndTime=2026-11-06 13:10:00\r\n"
             "items[1].Events[0]=VideoMotion\r\n")

    class S:
        def get(self, url, params=None, **kw):
            calls.append(params["action"])
            if params["action"] == "factory.create":
                return _Resp(200, "result=3456\r\n")
            if params["action"] == "findNextFile":
                return _Resp(200, found)
            return _Resp(200, "OK")
    monkeypatch.setattr(nvr, "_session", lambda cam: S())
    segs = nvr.search_recordings(dict(HIK, brand="Dahua"), date(2026, 11, 6))
    assert [(s["start"].hour, s["kind"]) for s in segs] == [(7, "continuous"), (13, "motion")]
    assert calls[-2:] == ["close", "destroy"]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_play_save_clip_and_snapshot_from_recorder(qtbot, tmp_path, monkeypatch):
    import cv2
    import numpy as np
    from app.config import Config
    from app.ui.pages import nvr_playback as page_mod
    if not shutil.which("/usr/bin/python3.12"):
        pytest.skip("needs the GStreamer test server")
    img = str(tmp_path / "p.png")
    cv2.imwrite(img, np.full((240, 320, 3), (30, 90, 160), np.uint8))
    port = _free_port()
    srv = subprocess.Popen(["/usr/bin/python3.12", "tests/rtsp_test_server.py", str(port), img,
                            "h264", "10"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.5)
    asked = []

    def fake_url(cam, start, end=None):
        asked.append(start)
        return f"rtsp://127.0.0.1:{port}/live"
    monkeypatch.setattr(page_mod, "playback_url", fake_url)
    monkeypatch.setattr(page_mod, "search_recordings", lambda cam, day: [
        {"start": datetime(2026, 11, 6, 8), "end": datetime(2026, 11, 6, 9), "kind": "continuous"}])
    tab = page_mod.NVRPlaybackTab()
    qtbot.addWidget(tab)
    try:
        tab.set_cameras([HIK, {"id": 2, "name": "Webcam", "brand": "Generic RTSP",
                               "host": "x"}])
        assert tab.cam_cb.count() == 1                        # only recorders that can play
        when = datetime(2026, 11, 6, 8, 15, 0)
        assert tab.open_camera_at(1, when)
        qtbot.waitUntil(lambda: tab.video.has_image(), timeout=20000)
        assert asked[0] == when
        qtbot.waitUntil(lambda: tab._pos is not None and tab._pos > when, timeout=10000)
        tab._snapshot()
        assert list(Path(Config.SNAPSHOTS_DIR).glob("nvr_1_*.jpg"))
        tab.btn_clip.setChecked(True)
        qtbot.wait(1500)
        tab.btn_clip.setChecked(False)
        qtbot.wait(500)
        clips = list(Path(Config.RECORDINGS_DIR, "exports").glob("*.mp4"))
        assert clips and clips[0].stat().st_size > 1000
        before = tab._pos
        tab._jump(-30)
        assert asked[-1] < before
        assert tab.found.text().startswith("1 recorded periods")
    finally:
        tab.shutdown()
        srv.terminate()
        srv.wait()


class _KeepAliveServer:
    """RTSP server that streams dummy RTP and records what the client sends;
    it refuses GET_PARAMETER like some recorders do."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.methods, self.rtcp = [], []
        import threading
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        import threading
        conn, _ = self.sock.accept()
        playing = threading.Event()

        def pump():
            playing.wait()
            seq = 0
            try:
                while True:
                    seq += 1
                    pkt = (b"\x80\xe0" + seq.to_bytes(2, "big") + (seq * 3000).to_bytes(4, "big")
                           + b"\x11\x22\x33\x44" + b"\x41" + b"\x00" * 20)
                    conn.sendall(b"$\x00" + len(pkt).to_bytes(2, "big") + pkt)
                    time.sleep(0.05)
            except OSError:
                pass
        threading.Thread(target=pump, daemon=True).start()
        buf = b""
        with conn:
            while True:
                try:
                    data = conn.recv(4096)
                except OSError:
                    return
                if not data:
                    return
                buf += data
                while buf:
                    if buf[:1] == b"$":
                        if len(buf) < 4:
                            break
                        n = int.from_bytes(buf[2:4], "big")
                        if len(buf) < 4 + n:
                            break
                        self.rtcp.append((buf[1], buf[4:4 + n]))
                        buf = buf[4 + n:]
                        continue
                    if b"\r\n\r\n" not in buf:
                        break
                    req, buf = buf.split(b"\r\n\r\n", 1)
                    text = req.decode()
                    method = text.split(" ")[0]
                    self.methods.append(method)
                    cseq = re.search(r"CSeq: (\d+)", text).group(1)
                    if method == "DESCRIBE":
                        sdp = ("v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
                               "a=control:trackID=1\r\n")
                        reply = (f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nContent-Type: "
                                 f"application/sdp\r\nContent-Length: {len(sdp)}\r\n\r\n{sdp}")
                    elif method == "GET_PARAMETER":
                        reply = f"RTSP/1.0 501 Not Implemented\r\nCSeq: {cseq}\r\n\r\n"
                    else:
                        reply = (f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nSession: 77\r\n"
                                 f"Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n\r\n")
                    conn.sendall(reply.encode())
                    if method == "PLAY":
                        playing.set()

    def close(self):
        self.sock.close()


def test_direct_decoder_keeps_the_session_alive(monkeypatch):
    from app.core import rtp_capture
    if not rtp_capture.AVAILABLE:
        pytest.skip("needs PyAV")
    monkeypatch.setattr(rtp_capture, "KEEPALIVE_S", 0.4)
    monkeypatch.setattr(rtp_capture, "RTCP_RR_S", 0.3)
    srv = _KeepAliveServer()
    cap = rtp_capture.RTPCapture(f"rtsp://127.0.0.1:{srv.port}/Streaming/tracks/101/"
                                 "?starttime=20261106T080000Z", read_timeout_s=3)
    try:
        assert cap.isOpened(), cap.error
        deadline = time.time() + 6
        while time.time() < deadline and not (
                "OPTIONS" in srv.methods and len(srv.rtcp) >= 2):
            time.sleep(0.1)
        assert "GET_PARAMETER" in srv.methods and "OPTIONS" in srv.methods  # fell back
        ch, rr = srv.rtcp[0]
        assert ch == 1 and rr[1] == 201 and rr[8:12] == b"\x11\x22\x33\x44"  # receiver report
        assert int.from_bytes(rr[16:20], "big") > 0                            # highest seq
        assert cap.error is None
    finally:
        cap.release()
        srv.close()


def test_playback_continues_when_the_recorder_ends_the_session(qtbot, monkeypatch):
    import numpy as np
    from app.core import stream_manager as smod
    from app.ui.pages import nvr_playback as page_mod

    class ShortSession:                       # 15 pictures, then the NVR hangs up
        def __init__(self):
            self.left = 15

        def isOpened(self):
            return True

        def read(self):
            time.sleep(0.02)
            if self.left <= 0:
                return False, None
            self.left -= 1
            return True, np.full((90, 160, 3), 120 + self.left, np.uint8)

        def release(self):
            pass
    monkeypatch.setattr(smod, "open_capture", lambda *a, **k: ShortSession())
    asked = []
    start = datetime(2026, 11, 6, 8, 0, 0)
    t = page_mod.PlaybackThread("rtsp://nvr/first", start,
                                url_for=lambda s: asked.append(s) or "rtsp://nvr/next")
    ended, positions = [], []
    t.finished_playing.connect(ended.append)
    t.position.connect(positions.append)
    t.start()
    try:
        qtbot.waitUntil(lambda: bool(ended), timeout=20000)
    finally:
        t.stop()
        t.wait(5000)
    assert len(asked) == page_mod.MAX_RESUMES                  # continued each time
    assert all(b > a for a, b in zip(asked, asked[1:]))        # from where it was
    assert asked[0] > start and positions[-1] > asked[-1]
    assert ended == ["The recording ended."]
