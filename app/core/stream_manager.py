import logging
import os
import time
from typing import Callable, Dict, List, Optional

# RTSP over TCP survives packet loss (Wi-Fi) far better than the UDP default.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QThread, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402

logger = logging.getLogger(__name__)

OPEN_TIMEOUT_MS = 10_000
READ_TIMEOUT_MS = 10_000
DISPLAY_MAX_WIDTH = 1280


def open_capture(source: str) -> cv2.VideoCapture:
    if isinstance(source, str) and "://" in source:
        return cv2.VideoCapture(source, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS,
        ])
    return cv2.VideoCapture(source)


def to_qimage(frame: np.ndarray, max_width: int = 0) -> QImage:
    if max_width and frame.shape[1] > max_width:
        scale = max_width / frame.shape[1]
        frame = cv2.resize(frame, (max_width, int(frame.shape[0] * scale)),
                           interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    # .copy() detaches the QImage from the numpy buffer, which is freed
    # as soon as this function returns.
    return QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()


class StreamThread(QThread):
    frame_ready = pyqtSignal(int, QImage)
    status_changed = pyqtSignal(int, bool, str)

    def __init__(self, camera_id: int, rtsp_url: str,
                 listeners: List[Callable], reconnect_delay: float = 2):
        super().__init__()
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self._listeners = listeners
        self._base_delay = max(1.0, float(reconnect_delay))
        self._running = False

    def run(self):
        self._running = True
        delay = self._base_delay
        while self._running:
            cap = None
            try:
                cap = open_capture(self.rtsp_url)
                if not cap.isOpened():
                    self.status_changed.emit(self.camera_id, False, "Cannot open stream")
                else:
                    self.status_changed.emit(self.camera_id, True, "Connected")
                    delay = self._base_delay
                    while self._running:
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            break
                        for listener in list(self._listeners):
                            try:
                                listener(self.camera_id, frame)
                            except Exception:
                                logger.exception("Frame listener failed")
                        self.frame_ready.emit(
                            self.camera_id, to_qimage(frame, DISPLAY_MAX_WIDTH))
                    if self._running:
                        self.status_changed.emit(self.camera_id, False, "Stream lost")
            except Exception as e:
                logger.error("Stream cam %d: %s", self.camera_id, e)
                self.status_changed.emit(self.camera_id, False, str(e))
            finally:
                if cap is not None:
                    cap.release()

            if self._running:
                self._sleep(delay)
                delay = min(delay * 2, 30)

    def _sleep(self, seconds: float):
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(0.1)

    def request_stop(self):
        self._running = False


class StreamManager:
    """Owns one StreamThread per camera.

    Frame listeners are called on the stream thread for every decoded frame,
    so they must be fast and thread-safe (the AI worker only stores the frame).
    """

    def __init__(self):
        self._streams: Dict[int, StreamThread] = {}
        self._retiring: List[StreamThread] = []
        self.frame_listeners: List[Callable] = []
        self.reconnect_delay = 2

    def add_frame_listener(self, cb: Callable):
        if cb not in self.frame_listeners:
            self.frame_listeners.append(cb)

    def start_stream(self, camera_id: int, rtsp_url: str,
                     on_frame=None, on_status=None) -> StreamThread:
        self.stop_stream(camera_id)
        thread = StreamThread(camera_id, rtsp_url, self.frame_listeners,
                              self.reconnect_delay)
        if on_frame:
            thread.frame_ready.connect(on_frame)
        if on_status:
            thread.status_changed.connect(on_status)
        self._streams[camera_id] = thread
        thread.start()
        return thread

    def stop_stream(self, camera_id: int):
        thread = self._streams.pop(camera_id, None)
        if thread is None:
            return
        # Never block the UI waiting for a read that may take READ_TIMEOUT_MS;
        # keep a reference until the thread exits so Qt doesn't destroy it early.
        thread.request_stop()
        try:
            thread.frame_ready.disconnect()
            thread.status_changed.disconnect()
        except TypeError:
            pass
        self._retiring.append(thread)
        thread.finished.connect(lambda t=thread: self._forget(t))

    def _forget(self, thread: StreamThread):
        if thread in self._retiring:
            self._retiring.remove(thread)

    def stop_all(self, wait_ms: int = 0):
        for cid in list(self._streams):
            self.stop_stream(cid)
        if wait_ms:
            deadline = time.monotonic() + wait_ms / 1000
            for t in list(self._retiring):
                remaining = max(0, int((deadline - time.monotonic()) * 1000))
                t.wait(remaining)

    def is_streaming(self, camera_id: int) -> bool:
        t = self._streams.get(camera_id)
        return t is not None and t.isRunning()

    def active_count(self) -> int:
        return sum(1 for t in self._streams.values() if t.isRunning())

    @staticmethod
    def snapshot(rtsp_url: str) -> Optional[np.ndarray]:
        cap = open_capture(rtsp_url)
        try:
            if not cap.isOpened():
                return None
            ok, frame = cap.read()
            return frame if ok else None
        finally:
            cap.release()
