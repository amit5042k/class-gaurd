"""Devices that hang up on an RTSP request (seen on a Hikvision DVR)."""
import base64
import re
import socket
import threading

import pytest


class HangupDVR:
    """Closes the connection for /Streaming/Channels paths (old firmware) or for
    requests without a login (login-first devices); answers the rest."""

    def __init__(self, mode="old_path"):
        self.mode = mode
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.requests = []
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
            while b"\r\n\r\n" not in buf:
                data = conn.recv(4096)
                if not data:
                    return
                buf += data
            req = buf.decode()
            self.requests.append(req.split("\r\n")[0])
            url = req.split(" ")[1]
            cseq = re.search(r"CSeq: (\d+)", req).group(1)
            if self.mode == "old_path" and "/Streaming/Channels/" in url:
                return                                    # hang up
            if self.mode == "login_first":
                auth = re.search(r"Authorization: Basic (\S+)", req)
                if not auth or base64.b64decode(auth.group(1)) != b"admin:pw":
                    return                                # hang up
            sdp = "v=0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
            conn.sendall((f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nContent-Length: {len(sdp)}"
                          f"\r\n\r\n{sdp}").encode())

    def url(self, path="/Streaming/Channels/502"):
        return f"rtsp://admin:pw@127.0.0.1:{self.port}{path}"

    def close(self):
        self.sock.close()


@pytest.fixture(autouse=True)
def _clear_preauth():
    from app.protocols import rtsp_diag
    rtsp_diag.PREAUTH_HOSTS.clear()
    yield
    rtsp_diag.PREAUTH_HOSTS.clear()


def test_hangup_is_reported_with_advice():
    from app.protocols.rtsp_diag import CLOSED, describe_detail
    dvr = HangupDVR()
    try:
        d = describe_detail(dvr.url())
        assert d["kind"] == CLOSED and "locked after wrong passwords" in d["message"]
    finally:
        dvr.close()


def test_alternative_hikvision_addresses():
    from app.protocols.rtsp_diag import alternative_urls
    alts = alternative_urls("rtsp://a:b@10.0.5.8:554/Streaming/Channels/502")
    assert alts[0] == "rtsp://a:b@10.0.5.8:554/h264/ch5/sub/av_stream"
    assert "rtsp://a:b@10.0.5.8:554/ISAPI/Streaming/channels/502" in alts
    assert alternative_urls("rtsp://x/Streaming/Channels/1601")[0].endswith("/h264/ch16/main/av_stream")
    assert alternative_urls("rtsp://x/cam/realmonitor?channel=1&subtype=0") == []


def test_old_firmware_path_is_found():
    from app.protocols.rtsp_diag import OK, find_working_url
    dvr = HangupDVR("old_path")
    try:
        url, d, tried = find_working_url(dvr.url())
        assert d["kind"] == OK and url == dvr.url("/h264/ch5/sub/av_stream")
    finally:
        dvr.close()


def test_login_first_device_is_found_and_remembered():
    from app.protocols import rtsp_diag
    dvr = HangupDVR("login_first")
    try:
        url, d, _ = rtsp_diag.find_working_url(dvr.url())
        assert url == dvr.url() and d["kind"] == rtsp_diag.OK
        assert rtsp_diag.PREAUTH_HOSTS.get("127.0.0.1")
        assert rtsp_diag.describe_detail(dvr.url())["kind"] == rtsp_diag.OK   # remembered
    finally:
        dvr.close()


def test_diagnose_reports_the_working_address():
    from app.core.diagnose import run_diagnosis
    dvr = HangupDVR("old_path")
    try:
        report = run_diagnosis({"name": "First 1 - Channel 5", "brand": "Hikvision",
                                "channel": 5, "rtsp_url": dvr.url(), "password": "pw"},
                               stream_seconds=1)
        assert "This device answers on:" in report and "/h264/ch5/sub/av_stream" in report
        assert ":pw@" not in report                       # password hidden
    finally:
        dvr.close()


def test_stream_switches_to_working_address(qtbot, monkeypatch):
    from app.core import stream_manager as smod
    dvr = HangupDVR("old_path")
    opened = []

    class NoPic:
        def isOpened(self):
            return True

        def read(self):
            return False, None

        def release(self):
            pass
    monkeypatch.setattr(smod, "open_capture",
                        lambda url, *a, **k: opened.append(url) or NoPic())
    sm = smod.StreamManager()
    try:
        sm.start_stream(1, dvr.url())
        qtbot.waitUntil(lambda: bool(opened), timeout=15000)
        assert opened[0].endswith("/h264/ch5/sub/av_stream")
        qtbot.waitUntil(lambda: 1 in sm.learned_url, timeout=5000)
        assert sm.learned_url[1] == (dvr.url(), dvr.url("/h264/ch5/sub/av_stream"))
    finally:
        sm.stop_all(wait_ms=10000)
        dvr.close()
