import cv2
import time
import logging
from typing import Callable, Dict, List, Optional
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

logger = logging.getLogger(__name__)


class StreamThread(QThread):
    frame_ready = pyqtSignal(int, QImage)
    status_changed = pyqtSignal(int, bool, str)
    raw_frame = pyqtSignal(int, object)  # camera_id, np.ndarray

    def __init__(self, camera_id: int, rtsp_url: str):
        super().__init__()
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self._running = False
        self._cap: Optional[cv2.VideoCapture] = None
        self.frame_callbacks: List[Callable] = []

    def run(self):
        self._running = True
        delay = 2
        while self._running:
            try:
                self._cap = cv2.VideoCapture(self.rtsp_url)
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)
                if not self._cap.isOpened():
                    self.status_changed.emit(self.camera_id, False, "Cannot open stream")
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue

                self.status_changed.emit(self.camera_id, True, "Connected")
                delay = 2

                while self._running:
                    ret, frame = self._cap.read()
                    if not ret:
                        break
                    self.raw_frame.emit(self.camera_id, frame)
                    for cb in self.frame_callbacks:
                        try:
                            cb(self.camera_id, frame)
                        except Exception:
                            pass
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    h, w, ch = rgb.shape
                    img = QImage(rgb.data.tobytes(), w, h, ch * w,
                                 QImage.Format.Format_RGB888)
                    self.frame_ready.emit(self.camera_id, img)

            except Exception as e:
                logger.error("Stream cam %d: %s", self.camera_id, e)
            finally:
                if self._cap:
                    self._cap.release()
                    self._cap = None

            if self._running:
                self.status_changed.emit(self.camera_id, False, "Reconnecting...")
                time.sleep(delay)

    def stop(self):
        self._running = False
        if self._cap:
            self._cap.release()
        self.wait(3000)


class StreamManager:
    def __init__(self):
        self._streams: Dict[int, StreamThread] = {}

    def start_stream(self, camera_id: int, rtsp_url: str,
                     on_frame=None, on_status=None,
                     on_raw=None) -> StreamThread:
        self.stop_stream(camera_id)
        thread = StreamThread(camera_id, rtsp_url)
        if on_frame:
            thread.frame_ready.connect(on_frame)
        if on_status:
            thread.status_changed.connect(on_status)
        if on_raw:
            thread.raw_frame.connect(on_raw)
        self._streams[camera_id] = thread
        thread.start()
        return thread

    def stop_stream(self, camera_id: int):
        if camera_id in self._streams:
            self._streams[camera_id].stop()
            del self._streams[camera_id]

    def stop_all(self):
        for cid in list(self._streams):
            self.stop_stream(cid)

    def is_streaming(self, camera_id: int) -> bool:
        t = self._streams.get(camera_id)
        return t is not None and t.isRunning()

    def add_callback(self, camera_id: int, cb: Callable):
        if camera_id in self._streams:
            self._streams[camera_id].frame_callbacks.append(cb)

    @staticmethod
    def snapshot(rtsp_url: str) -> Optional[np.ndarray]:
        cap = cv2.VideoCapture(rtsp_url)
        if not cap.isOpened():
            return None
        ret, frame = cap.read()
        cap.release()
        return frame if ret else None
