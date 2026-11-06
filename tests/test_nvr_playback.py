"""Playback of recordings stored on the NVR."""
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
