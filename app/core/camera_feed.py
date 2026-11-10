"""Live picture for face enrollment: this PC's webcam or one of the CCTV
cameras / NVR channels (so a PC without a webcam can still enroll faces)."""
import logging
import sys
import time
from typing import Dict, List, Optional

import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from app.config import Config

logger = logging.getLogger(__name__)

WEBCAM_INDEXES = (0, 1, 2, 3)
FIRST_PICTURE_TIMEOUT_S = 15
FACE_BOX_EVERY_S = 0.3


def webcam_backends() -> List[int]:
    """Windows has several camera drivers; some webcams only answer one of them."""
    if sys.platform == "win32":
        return [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    if sys.platform == "darwin":
        return [cv2.CAP_AVFOUNDATION, cv2.CAP_ANY]
    return [cv2.CAP_V4L2, cv2.CAP_ANY]


def open_webcam():
    """(capture, description) for the first webcam that gives a picture, or (None, "")."""
    for index in WEBCAM_INDEXES:
        for backend in webcam_backends():
            try:
                cap = cv2.VideoCapture(index, backend)
            except Exception:
                continue
            if cap is not None and cap.isOpened():
                for _ in range(10):              # some webcams need a moment
                    ok, frame = cap.read()
                    if ok and frame is not None and frame.size:
                        return cap, f"webcam {index}"
                    time.sleep(0.1)
            if cap is not None:
                cap.release()
    return None, ""


def enrollment_url(cam: Dict) -> str:
    """Main stream when the brand allows it: faces need detail."""
    if cam.get("custom_rtsp_url") or cam.get("brand") not in Config.RTSP_TEMPLATES:
        return cam.get("rtsp_url", "")
    if not cam.get("host"):
        return cam.get("rtsp_url", "")
    return Config.build_rtsp_url(cam["brand"], cam["host"], cam.get("port") or 554,
                                 cam.get("username", ""), cam.get("password", ""),
                                 int(cam.get("channel") or 1), substream=False)


def largest_face_crop(frame: np.ndarray, boxes) -> np.ndarray:
    """The biggest face with room around it, enlarged if small, so a CCTV picture
    with several people can still be enrolled for the person in front."""
    if not boxes:
        return frame
    x1, y1, x2, y2 = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    w, h = x2 - x1, y2 - y1
    H, W = frame.shape[:2]
    crop = frame[max(0, y1 - h):min(H, y2 + h), max(0, x1 - w):min(W, x2 + w)]
    if crop.size == 0:
        return frame
    if h < 120:
        crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    return crop


class CameraFeed(QThread):
    frame_ready = pyqtSignal(object)       # BGR frame with face boxes drawn
    status = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, source: Dict, face_engine=None, parent=None):
        super().__init__(parent)
        self.source = source               # {"kind": "webcam"} or {"kind": "camera", "camera": {...}}
        self.fe = face_engine
        self._running = True
        self.last_frame: Optional[np.ndarray] = None
        self.boxes: List = []

    def stop(self):
        self._running = False

    def _open_camera(self):
        from app.core.stream_manager import is_placeholder_frame, open_capture
        from app.core.av_capture import AVAILABLE
        cam = self.source["camera"]
        url = self._usable_url(cam)
        for backend in (["rtp"] if AVAILABLE else []) + ["opencv"]:
            if not self._running:
                return None
            cap = open_capture(url, "tcp", backend)
            if not cap.isOpened():
                cap.release()
                continue
            t0 = time.monotonic()
            while self._running and time.monotonic() - t0 < FIRST_PICTURE_TIMEOUT_S:
                ok, frame = cap.read()
                if not ok:
                    break
                if not is_placeholder_frame(frame):
                    self.last_frame = frame
                    return cap
            cap.release()
        return None

    @staticmethod
    def _usable_url(cam: Dict) -> str:
        """Main stream if the device answers for it; else another address form of
        it (older DVRs); else the address Live View uses."""
        url = enrollment_url(cam)
        fallback = cam.get("rtsp_url") or url
        if not str(url).startswith("rtsp") or url == fallback:
            return url
        try:
            from app.protocols.rtsp_diag import CLOSED, OK, describe, find_working_url
            kind, _ = describe(url, timeout=3)
            if kind == CLOSED:
                found, _d, _t = find_working_url(url, timeout=3)
                return found or fallback
            return url if kind == OK else fallback
        except Exception:
            return fallback

    def run(self):
        if self.source.get("kind") == "webcam":
            self.status.emit("Looking for a webcam…")
            cap, desc = open_webcam()
            if cap is None:
                self.failed.emit("No webcam found on this PC. Choose a CCTV camera in the list "
                                 "instead, or use 'Enroll from Photo File'.")
                return
        else:
            name = self.source["camera"].get("name", "camera")
            self.status.emit(f"Connecting to {name}…")
            cap = self._open_camera()
            if cap is None:
                if self._running:
                    self.failed.emit(f"No picture from {name}. Check it in Live View, or "
                                     "choose another camera.")
                return
            desc = name
        self.status.emit(f"Live: {desc}. Ask the person to look at the camera, then press "
                         "Capture & Enroll.")
        last_boxes = 0.0
        last_emit = 0.0
        try:
            while self._running:
                if self.last_frame is None:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    self.last_frame = frame
                frame = self.last_frame
                now = time.monotonic()
                if self.fe is not None and now - last_boxes > FACE_BOX_EVERY_S:
                    last_boxes = now
                    try:
                        self.boxes = self._detect(frame)
                    except Exception:
                        self.boxes = []
                if now - last_emit >= 0.08:
                    last_emit = now
                    shown = frame.copy()
                    for x1, y1, x2, y2 in self.boxes:
                        cv2.rectangle(shown, (x1, y1), (x2, y2), (60, 220, 60),
                                      max(2, frame.shape[1] // 400))
                    self.frame_ready.emit(shown)
                ok, frame = cap.read()
                if not ok or frame is None:
                    if self._running:
                        self.failed.emit("The picture stopped. Press Start again.")
                    break
                self.last_frame = frame
        finally:
            cap.release()

    def _detect(self, frame):
        """Face boxes on a smaller copy (fast), in full-frame pixels."""
        h, w = frame.shape[:2]
        scale = min(1.0, 960 / max(w, 1))
        small = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame
        return [tuple(int(v / scale) for v in b) for b in self.fe.detect_faces(small)]
