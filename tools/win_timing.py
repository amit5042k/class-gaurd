"""Temporary CI diagnostic: how long do failing RTSP opens take on this OS?"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2  # noqa: E402

from app.core.stream_manager import open_capture  # noqa: E402
from app.protocols.rtsp_diag import describe  # noqa: E402


def timed(label, fn):
    t0 = time.monotonic()
    try:
        r = fn()
    except Exception as e:
        r = f"error {e}"
    print(f"{label}: {time.monotonic() - t0:.2f}s -> {r}", flush=True)


url = "rtsp://127.0.0.1:1/nothing"
timed("raw cv2 FFMPEG open", lambda: cv2.VideoCapture(url, cv2.CAP_FFMPEG).isOpened())
timed("raw cv2 open+timeouts", lambda: cv2.VideoCapture(url, cv2.CAP_FFMPEG, [
    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 10000]).isOpened())
timed("open_capture tcp", lambda: open_capture(url).isOpened())
timed("open_capture udp", lambda: open_capture(url, "udp").isOpened())
timed("describe", lambda: describe(url))
timed("describe 10.255.255.1 (no host)", lambda: describe("rtsp://10.255.255.1/x", 3))
