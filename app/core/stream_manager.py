import logging
import os
import time
from typing import Callable, Dict, List, Optional

import threading

# RTSP over TCP survives packet loss (Wi-Fi) far better than UDP. OpenCV reads
# this variable on every open, so a camera can be opened over UDP by changing
# it briefly (see _TransportLock).
_DEFAULT_OPTIONS = "rtsp_transport;tcp"
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", _DEFAULT_OPTIONS)

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QThread, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402

logger = logging.getLogger(__name__)

OPEN_TIMEOUT_MS = 10_000
READ_TIMEOUT_MS = 10_000
DISPLAY_MAX_WIDTH = 1280
DISPLAY_FPS = 15  # screen refresh cap; recording and AI still get every frame
CONNECTING = "Connecting…"


class _TransportLock:
    """Opens over TCP run in parallel; an open over UDP briefly has the
    process-wide FFmpeg options to itself."""

    def __init__(self):
        self._cond = threading.Condition()
        self._shared = 0
        self._exclusive = False

    def shared(self):
        lock = self

        class _Ctx:
            def __enter__(self):
                with lock._cond:
                    while lock._exclusive:
                        lock._cond.wait()
                    lock._shared += 1

            def __exit__(self, *exc):
                with lock._cond:
                    lock._shared -= 1
                    lock._cond.notify_all()
        return _Ctx()

    def exclusive(self):
        lock = self

        class _Ctx:
            def __enter__(self):
                with lock._cond:
                    while lock._exclusive or lock._shared:
                        lock._cond.wait()
                    lock._exclusive = True

            def __exit__(self, *exc):
                with lock._cond:
                    lock._exclusive = False
                    lock._cond.notify_all()
        return _Ctx()


_transport_lock = _TransportLock()


def open_capture(source: str, transport: str = "tcp") -> cv2.VideoCapture:
    if not (isinstance(source, str) and "://" in source):
        return cv2.VideoCapture(source)
    params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS,
              cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS]
    if transport == "udp" and source.startswith("rtsp"):
        with _transport_lock.exclusive():
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;udp"
            try:
                return cv2.VideoCapture(source, cv2.CAP_FFMPEG, params)
            finally:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = _DEFAULT_OPTIONS
    with _transport_lock.shared():
        return cv2.VideoCapture(source, cv2.CAP_FFMPEG, params)


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
    transport_learned = pyqtSignal(int, str)   # camera_id, "tcp" | "udp"

    def __init__(self, camera_id: int, rtsp_url: str,
                 listeners: List[Callable], reconnect_delay: float = 2,
                 transport: str = "auto", first: str = "tcp"):
        super().__init__()
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self._listeners = listeners
        self._base_delay = max(1.0, float(reconnect_delay))
        self._running = False
        self.last_frame = None
        self.transport = transport if transport in ("tcp", "udp") else "auto"
        self._current = "udp" if self.transport == "udp" or \
            (self.transport == "auto" and first == "udp") else "tcp"

    def _switch_transport(self) -> bool:
        """Auto mode: try the other RTSP transport after a failed attempt."""
        if self.transport != "auto" or not str(self.rtsp_url).startswith("rtsp"):
            return False
        self._current = "udp" if self._current == "tcp" else "tcp"
        return True

    def run(self):
        self._running = True
        delay = self._base_delay
        last_display = 0.0
        failures = 0
        reason = ""
        last_kind = ""
        learned = False
        self.status_changed.emit(self.camera_id, False, CONNECTING)
        while self._running:
            cap = None
            got_frame = False
            try:
                cap = open_capture(self.rtsp_url, self._current)
                if not cap.isOpened():
                    # Ask the device why (on the first failure and every 5th after),
                    # so the tile says "wrong password" or "no camera on this channel".
                    if failures % 5 == 0:
                        last_kind, reason = self._diagnose()
                    # Some recorders refuse one RTSP transport (e.g. 461 Unsupported
                    # transport), so alternate unless the device gave a reason that
                    # another transport can't fix.
                    if last_kind not in ("unreachable", "auth", "not_found"):
                        self._switch_transport()
                    failures += 1
                    self.status_changed.emit(self.camera_id, False,
                                             reason or "Cannot open stream")
                else:
                    bad_reads = 0
                    while self._running:
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            bad_reads += 1
                            # A single failed read is often a glitch; give up on the
                            # connection only after several in a row.
                            if bad_reads >= 3 or not got_frame:
                                break
                            continue
                        bad_reads = 0
                        if not got_frame:
                            got_frame = True
                            failures, reason, delay = 0, "", self._base_delay
                            label = "Connected" + (" (UDP)" if self._current == "udp" else "")
                            self.status_changed.emit(self.camera_id, True, label)
                            if self.transport == "auto" and not learned:
                                learned = True
                                self.transport_learned.emit(self.camera_id, self._current)
                        self.last_frame = frame
                        for listener in list(self._listeners):
                            try:
                                listener(self.camera_id, frame)
                            except Exception:
                                logger.exception("Frame listener failed")
                        now = time.monotonic()
                        if now - last_display >= 1 / DISPLAY_FPS:
                            last_display = now
                            self.frame_ready.emit(
                                self.camera_id, to_qimage(frame, DISPLAY_MAX_WIDTH))
                    if self._running:
                        if got_frame:
                            self.status_changed.emit(self.camera_id, False, "Stream lost")
                        else:
                            failures += 1
                            other = "UDP" if self._current == "tcp" else "TCP"
                            msg = ("Connected, but no picture arrived over "
                                   f"{self._current.upper()}.")
                            if self._switch_transport():
                                msg += f" Trying {other}…"
                            self.status_changed.emit(self.camera_id, False, msg)
                            logger.warning("Camera %s: opened over %s but no frames",
                                           self.camera_id, self._current)
            except Exception as e:
                logger.error("Stream cam %d: %s", self.camera_id, e)
                self.status_changed.emit(self.camera_id, False, str(e))
            finally:
                if cap is not None:
                    cap.release()

            if self._running:
                self._sleep(delay)
                delay = min(delay * 2, 30)

    def _diagnose(self):
        """(kind, message) from asking the device; ("", "") if not RTSP."""
        if not str(self.rtsp_url).startswith("rtsp"):
            return "", ""
        try:
            from app.protocols.rtsp_diag import OK, describe
            kind, msg = describe(self.rtsp_url)
            if kind == OK:
                msg = ("The device offers this stream but it couldn't be opened over "
                       f"{self._current.upper()}. Retrying"
                       + (" with TCP and UDP." if self.transport == "auto" else "."))
            return kind, msg
        except Exception:
            logger.exception("RTSP diagnosis failed")
            return "", ""

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
        self.learned_transport: Dict[int, str] = {}
        self.reconnect_delay = 2

    def add_frame_listener(self, cb: Callable):
        if cb not in self.frame_listeners:
            self.frame_listeners.append(cb)

    def start_stream(self, camera_id: int, rtsp_url: str,
                     on_frame=None, on_status=None, transport: str = "auto",
                     on_transport=None) -> StreamThread:
        self.stop_stream(camera_id)
        thread = StreamThread(camera_id, rtsp_url, self.frame_listeners,
                              self.reconnect_delay, transport,
                              self.learned_transport.get(camera_id, "tcp"))
        thread.transport_learned.connect(
            lambda cid, t: self.learned_transport.__setitem__(cid, t))
        if on_transport:
            thread.transport_learned.connect(on_transport)
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
        for sig in (thread.frame_ready, thread.status_changed, thread.transport_learned):
            try:
                sig.disconnect()
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

    def latest_frame(self, camera_id: int) -> Optional[np.ndarray]:
        t = self._streams.get(camera_id)
        return t.last_frame if t is not None and t.isRunning() else None

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
