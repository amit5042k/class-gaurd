"""Regression tests for the 2.3.4 fixes."""
import json
import threading
import time

import numpy as np


def test_settings_survive_simultaneous_saves():
    from app.config import Config
    errors = []

    def save(i):
        try:
            for _ in range(25):
                Config.LEARNED_STREAMS = {str(i): {"backend": "rtp"}}
                Config.save_settings()
        except Exception as e:           # pragma: no cover - the failure being tested
            errors.append(e)
    threads = [threading.Thread(target=save, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    with open(Config.SETTINGS_PATH, encoding="utf-8") as f:
        assert "LEARNED_STREAMS" in json.load(f)          # still valid JSON


def test_slow_still_picture_does_not_delay_video(qtbot, monkeypatch):
    from app.core import stream_manager as smod
    frame = np.full((90, 160, 3), (30, 90, 160), np.uint8)

    class Live:
        def isOpened(self):
            return True

        def read(self):
            time.sleep(0.02)
            return True, frame

        def release(self):
            pass
    monkeypatch.setattr(smod, "_open_guarded", lambda *a, **k: Live())
    monkeypatch.setattr(smod.StreamThread, "_precheck", lambda self: ("", ""))

    def slow_snapshot():                  # e.g. the device's web port doesn't answer
        time.sleep(4)
        return np.zeros((90, 160, 3), np.uint8)
    statuses = []
    sm = smod.StreamManager()
    t0 = time.monotonic()
    sm.start_stream(1, "rtsp://cam/x", snapshot=slow_snapshot,
                    on_status=lambda c, ok, m: statuses.append((time.monotonic() - t0, ok, m)))
    try:
        qtbot.waitUntil(lambda: any(ok for _, ok, _m in statuses), timeout=3000)
        qtbot.wait(4500)                  # the late still must not undo "Connected"
        assert statuses[-1][1] is True
        assert not any(m == smod.PREVIEW_MSG for _, _ok, m in statuses)
    finally:
        sm.stop_all(wait_ms=5000)


def test_address_search_continues_after_not_found(monkeypatch):
    from app.protocols import rtsp_diag as rd
    answers = {"/Streaming/Channels/502": rd.CLOSED, "/h264/ch5/sub/av_stream": rd.NOT_FOUND,
               "/ISAPI/Streaming/channels/502": rd.OK}

    def fake(url, timeout=4.0, preauth=None):
        kind = next(k for path, k in answers.items() if url.endswith(path))
        return {"kind": kind, "message": kind, "code": None, "media": [], "auth": "none"}
    monkeypatch.setattr(rd, "describe_detail", fake)
    url, d, tried = rd.find_working_url("rtsp://a:b@10.0.5.8:554/Streaming/Channels/502")
    assert url.endswith("/ISAPI/Streaming/channels/502") and d["kind"] == rd.OK
    answers["/ISAPI/Streaming/channels/502"] = rd.AUTH        # nothing works: best reason
    answers["/h264/ch05/sub/av_stream"] = rd.CLOSED
    url, d, _ = rd.find_working_url("rtsp://a:b@10.0.5.8:554/Streaming/Channels/502")
    assert url is None and d["kind"] == rd.AUTH


def test_full_frame_rate_is_not_changed_by_saving(qtbot, monkeypatch):
    from app.protocols import device_config as dc
    from app.ui.pages import device_config_dialog as dlg_mod
    video = {"codec": "H.264", "width": 640, "height": 360, "bitrate_type": "VBR",
             "bitrate": 512, "fps": 0.0, "gop": 50, "quality": "Medium", "smart_codec": None}

    class Cfg:
        def get_video(self, ch, sub):
            return dict(video)

        def video_options(self, ch, sub):
            return {"fps": [25.0, 20.0, 15.0]}
    monkeypatch.setattr(dlg_mod, "device_config", lambda dev: Cfg())
    d = dlg_mod.DeviceConfigDialog({"name": "DVR", "brand": "Hikvision", "host": "x"})
    qtbot.addWidget(d)
    qtbot.waitUntil(lambda: d.btn_save.isEnabled(), timeout=5000)
    assert d.fps.currentText() == "Full frame rate" and d.changes() == {}
    d.gop.setValue(40)
    assert d.changes() == {"gop": 40}                         # frame rate left alone
    assert dc.recommended(video)["gop"] == 40
