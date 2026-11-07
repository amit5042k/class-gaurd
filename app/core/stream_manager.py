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
# Let OpenCV keep decoding while it waits for a keyframe (default 64 attempts is
# too few for streams with long keyframe intervals such as H.264+/Smart Codec).
os.environ.setdefault("OPENCV_FFMPEG_DECODE_ATTEMPTS", "20000")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PyQt6.QtCore import QThread, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402

logger = logging.getLogger(__name__)

OPEN_TIMEOUT_MS = 10_000
READ_TIMEOUT_MS = 10_000
DISPLAY_MAX_WIDTH = 1280
UDP_BUFFER = 4 * 1024 * 1024
DISPLAY_FPS = 15  # screen refresh cap; recording and AI still get every frame
CONNECTING = "Connecting…"
WAITING_KEYFRAME = ("Connected. Waiting for a full picture from the camera (keyframe)…")
NO_KEYFRAME_MSG = ("Connected, but the camera hasn't sent a full picture yet. On the NVR/camera, "
                   "turn off H.264+ / H.265+ / Smart Codec and set the I-frame interval "
                   "to 25–50 for this stream.")
SNAPSHOT_MSG = ("Still pictures (about 1 per second): the NVR's video arrives without full "
                "pictures. For live video turn off H.264+ / Smart Codec on the NVR and set "
                "the I-frame interval to 25–50.")
SNAPSHOT_SECONDS = 600     # then try live video again
GREY_WARN_S = 6            # grey this long: try the next decoder / still pictures
GREY_SHOW_S = 12           # grey this long: show the grey pictures anyway
PREVIEW_MSG = "Connecting… (showing a still picture until live video starts)"
SILENT_MSG = ("The device accepts the connection but sends no video data. Is the camera "
              "on this channel online? Security software on this PC can also block it.")


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


_ENV_READ_WINDOW_S = 0.3  # OpenCV reads the options as the open starts


def _open_guarded(source: str, params, options: str) -> cv2.VideoCapture:
    """Open with the given FFmpeg options without blocking other cameras.

    The options are process-wide, but OpenCV only reads them at the start of
    an open, so the lock is held just for that moment, not for the whole
    (possibly 10 s) connection attempt.
    """
    result = {}

    def target():
        result["cap"] = cv2.VideoCapture(source, cv2.CAP_FFMPEG, params)

    worker = threading.Thread(target=target, daemon=True)
    special = options != _DEFAULT_OPTIONS
    ctx = _transport_lock.exclusive() if special else _transport_lock.shared()
    with ctx:
        if special:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = options
        try:
            worker.start()
            worker.join(_ENV_READ_WINDOW_S)
        finally:
            if special:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = _DEFAULT_OPTIONS
    worker.join()
    return result["cap"]


def open_capture(source: str, transport: str = "tcp", backend: str = "opencv"):
    if backend == "rtp":
        from app.core.rtp_capture import RTPCapture
        return RTPCapture(source, OPEN_TIMEOUT_MS / 1000, READ_TIMEOUT_MS / 1000)
    if backend == "pyav":
        from app.core.av_capture import AVCapture
        return AVCapture(source, transport, OPEN_TIMEOUT_MS / 1000, READ_TIMEOUT_MS / 1000)
    if not (isinstance(source, str) and "://" in source):
        return cv2.VideoCapture(source)
    params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS,
              cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS]
    udp = transport == "udp" and source.startswith("rtsp")
    # A big receive buffer stops UDP from dropping parts of the large keyframes,
    # which otherwise leaves only grey pictures.
    return _open_guarded(source, params,
                         f"rtsp_transport;udp|buffer_size;{UDP_BUFFER}" if udp else _DEFAULT_OPTIONS)


def is_placeholder_frame(frame: np.ndarray) -> bool:
    """True for the flat mid-grey image a decoder outputs before it has a
    keyframe (only moving bits such as an on-screen clock are drawn)."""
    small = cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA).astype(np.int16)
    grey = np.all(np.abs(small - 128) <= 6, axis=2)
    return float(grey.mean()) > 0.85


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


BACKENDS = ("opencv", "rtp", "pyav")
BACKEND_LABELS = {"opencv": "", "rtp": " (direct decoder)", "pyav": " (alt. decoder)"}
DECODER_NAMES = {"opencv": "the standard decoder", "rtp": "the direct decoder",
                 "pyav": "the alternative decoder"}


def preferred_decoder() -> str:
    """First decoder for a camera nothing is known about yet."""
    from app.config import Config
    pref = getattr(Config, "PREFERRED_DECODER", "rtp")
    if pref == "rtp":
        from app.core.av_capture import AVAILABLE
        return "rtp" if AVAILABLE else "opencv"
    return pref if pref in BACKENDS else "opencv"


class _Retry(Exception):
    """Skip this attempt and wait before the next one."""


class StreamThread(QThread):
    frame_ready = pyqtSignal(int, QImage)
    status_changed = pyqtSignal(int, bool, str)
    transport_learned = pyqtSignal(int, str)   # camera_id, "tcp" | "udp"
    backend_learned = pyqtSignal(int, str)     # camera_id, "opencv" | "pyav"
    url_learned = pyqtSignal(int, str)         # camera_id, address form that works

    def __init__(self, camera_id: int, rtsp_url: str,
                 listeners: List[Callable], reconnect_delay: float = 2,
                 transport: str = "auto", first: str = "tcp", backend: str = "opencv",
                 keyframe_request: Optional[Callable] = None,
                 snapshot: Optional[Callable] = None):
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
        self._backend = backend if backend in BACKENDS else "opencv"
        self._first_backend = self._backend
        self._keyframe_request = keyframe_request
        self._snapshot = snapshot
        self._snapshot_ok = snapshot is not None
        self._snapshot_failures = 0
        self._last_display = 0.0

    def _order(self) -> List[str]:
        """Decoders to try, the one this camera started with first."""
        from app.core.av_capture import AVAILABLE
        usable = [b for b in BACKENDS
                  if b == "opencv" or (AVAILABLE and (b != "rtp" or self._direct_possible()))]
        first = self._first_backend if self._first_backend in usable else "opencv"
        return [first] + [b for b in usable if b != first]

    def _has_next_decoder(self) -> bool:
        order = self._order()
        return self._backend in order and order.index(self._backend) < len(order) - 1

    def _next_attempt(self) -> str:
        """After "connected but no picture": try the next decoder, then, once all
        were tried, the other transport."""
        order = self._order()
        i = order.index(self._backend) if self._backend in order else -1
        if i + 1 < len(order):
            self._backend = order[i + 1]
            return DECODER_NAMES[self._backend]
        self._backend = order[0]
        if self._switch_transport():
            return self._current.upper()
        return ""

    def _direct_possible(self) -> bool:
        from app.core.av_capture import AVAILABLE
        return AVAILABLE and str(self.rtsp_url).startswith("rtsp")

    def _switch_transport(self) -> bool:
        """Auto mode: try the other RTSP transport after a failed attempt."""
        if self.transport != "auto" or not str(self.rtsp_url).startswith("rtsp"):
            return False
        self._current = "udp" if self._current == "tcp" else "tcp"
        return True

    def run(self):
        self._running = True
        delay = self._base_delay
        failures = 0
        reason = ""
        last_kind = ""
        learned = False
        self.status_changed.emit(self.camera_id, False, CONNECTING)
        self._show_preview()
        while self._running:
            cap = None
            got_frame = False
            go_snapshot = False
            try:
                # Ask the device first: it answers in well under a second, while a
                # failed OpenCV open always waits the full timeout on Windows.
                pre_kind, pre_msg = self._precheck()
                if pre_kind == "closed":
                    pre_kind, pre_msg = self._find_address(pre_msg)
                    if pre_kind == "closed":
                        # Keep trying slowly (a device that locked this PC after wrong
                        # passwords stays locked while it is asked again), with still
                        # pictures meanwhile where the device offers them.
                        failures += 1
                        last_kind, reason = pre_kind, pre_msg
                        self.status_changed.emit(self.camera_id, False, pre_msg)
                        delay = max(delay, 30)
                        if self._snapshot_ok and self._snapshot_mode():
                            delay = self._base_delay
                        raise _Retry()
                # "Unreachable" is certain; for the others still let OpenCV try
                # every third attempt, in case a device answers our check oddly.
                if pre_kind == "unreachable" or (
                        pre_kind in ("auth", "not_found", "busy") and failures % 3 != 2):
                    failures += 1
                    last_kind, reason = pre_kind, pre_msg
                    self.status_changed.emit(self.camera_id, False, pre_msg)
                    raise _Retry()
                if pre_kind == "ok":
                    # Some devices briefly refuse a session right after another closes.
                    self._sleep(0.5)
                cap = open_capture(self.rtsp_url, self._current, self._backend)
                if not cap.isOpened():
                    # Ask the device why (on the first failure and every 5th after),
                    # so the tile says "wrong password" or "no camera on this channel".
                    if failures % 5 == 0:
                        last_kind, reason = self._diagnose()
                        if last_kind == "ok" and self._device_sends_nothing():
                            reason = SILENT_MSG
                    # Some recorders refuse one RTSP transport (e.g. 461 Unsupported
                    # transport), so alternate unless the device gave a reason that
                    # another transport can't fix.
                    if self._backend != "opencv" and self._has_next_decoder():
                        self._next_attempt()         # that decoder can't connect here
                    elif self._backend != "opencv":
                        self._backend = "opencv"
                    elif last_kind not in ("unreachable", "auth", "not_found"):
                        self._switch_transport()
                    failures += 1
                    self.status_changed.emit(self.camera_id, False,
                                             reason or "Cannot open stream")
                else:
                    bad_reads = 0
                    full_seen = False
                    grey_shown = False
                    waiting_since = None
                    warned = False
                    last_kf_request = 0.0
                    self._request_keyframe()
                    while self._running:
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            bad_reads += 1
                            # A single failed read is often a glitch; give up on the
                            # connection only after several in a row.
                            if bad_reads >= 3 or not (got_frame or waiting_since):
                                break
                            continue
                        bad_reads = 0
                        if not full_seen:
                            if is_placeholder_frame(frame):
                                # Don't show grey half-pictures; ask for a keyframe.
                                now = time.monotonic()
                                if waiting_since is None:
                                    waiting_since = now
                                    self.status_changed.emit(self.camera_id, False,
                                                             WAITING_KEYFRAME)
                                elif not warned and now - waiting_since > GREY_WARN_S:
                                    warned = True
                                    if self._has_next_decoder():
                                        break    # try the next decoder (no picture yet)
                                    if self._snapshot_ok:
                                        go_snapshot = True   # stills beat a grey tile
                                        break
                                    self.status_changed.emit(self.camera_id, False,
                                                             NO_KEYFRAME_MSG)
                                if now - last_kf_request > 8:
                                    last_kf_request = now
                                    self._request_keyframe()
                                if now - waiting_since < GREY_SHOW_S:
                                    continue
                                # A real flat grey scene, or no keyframes at all:
                                # show what we have rather than nothing.
                                grey_shown = True
                            full_seen = True
                        if not got_frame:
                            got_frame = True
                            failures, reason, delay = 0, "", self._base_delay
                            label = "Connected" + (" (UDP)" if self._current == "udp"
                                                   and self._backend != "rtp" else "") \
                                + BACKEND_LABELS.get(self._backend, "")
                            self.status_changed.emit(self.camera_id, True,
                                                     NO_KEYFRAME_MSG if grey_shown else label)
                            if not learned:
                                learned = True
                                if self.transport == "auto":
                                    self.transport_learned.emit(self.camera_id, self._current)
                                self.backend_learned.emit(self.camera_id, self._backend)
                        self._deliver(frame)
                    if self._running and not go_snapshot:
                        if got_frame:
                            self.status_changed.emit(self.camera_id, False, "Stream lost")
                        else:
                            failures += 1
                            tried = f"{self._current.upper()}" + \
                                {"pyav": " with the alternative decoder",
                                 "rtp": " with the direct decoder"}.get(self._backend, "")
                            nxt = self._next_attempt()
                            msg = f"Connected, but no picture arrived over {tried}."
                            silent = failures % 4 == 1 and self._device_sends_nothing()
                            if silent:
                                msg = SILENT_MSG
                            elif nxt:
                                msg += f" Trying {nxt}…"
                            self.status_changed.emit(self.camera_id, False, msg)
                            logger.warning("Camera %s: opened over %s but no frames",
                                           self.camera_id, tried)
                            delay = self._base_delay  # retry the next combination promptly
                            if failures >= 2 and self._snapshot_ok:
                                go_snapshot = True
            except _Retry:
                pass
            except Exception as e:
                logger.error("Stream cam %d: %s", self.camera_id, e)
                self.status_changed.emit(self.camera_id, False, str(e))
            finally:
                if cap is not None:
                    cap.release()

            if go_snapshot and self._running:
                if self._snapshot_mode():
                    failures, delay = 0, self._base_delay
                    continue
            if self._running:
                self._sleep(delay)
                delay = min(delay * 2, 30)

    def _deliver(self, frame, force_display: bool = False):
        self.last_frame = frame
        for listener in list(self._listeners):
            try:
                listener(self.camera_id, frame)
            except Exception:
                logger.exception("Frame listener failed")
        now = time.monotonic()
        if force_display or now - self._last_display >= 1 / DISPLAY_FPS:
            self._last_display = now
            self.frame_ready.emit(self.camera_id, to_qimage(frame, DISPLAY_MAX_WIDTH))

    def _show_preview(self):
        """Show a still picture from the device at once, while video connects."""
        if not self._snapshot_ok:
            return
        img = self._grab_snapshot()
        if img is not None and self._running:
            self._deliver(img, force_display=True)
            self.status_changed.emit(self.camera_id, False, PREVIEW_MSG)

    def _grab_snapshot(self):
        try:
            img = self._snapshot()
            return img if img is not None and getattr(img, "size", 0) else None
        except Exception as e:
            logger.info("Snapshot failed for camera %s: %s", self.camera_id, e)
            return None

    def _snapshot_mode(self) -> bool:
        """Show the device's still pictures instead of undecodable video for a
        while. False if the device gives no stills (then never try again)."""
        img = self._grab_snapshot()
        if img is None:
            # The NVR may be busy right then (many cameras at once): give up only
            # after several tries.
            self._snapshot_failures += 1
            if self._snapshot_failures >= 3:
                self._snapshot_ok = False
            return False
        self._snapshot_failures = 0
        logger.warning("Camera %s: video has no full pictures; showing still pictures",
                       self.camera_id)
        self.status_changed.emit(self.camera_id, True, SNAPSHOT_MSG)
        end = time.monotonic() + SNAPSHOT_SECONDS
        misses = 0
        while self._running and time.monotonic() < end:
            if img is not None:
                misses = 0
                self._deliver(img, force_display=True)
            else:
                misses += 1
                if misses >= 5:
                    self.status_changed.emit(self.camera_id, False, "Stream lost")
                    break
            self._sleep(1.0)
            if self._running:
                img = self._grab_snapshot()
        return True

    def _request_keyframe(self):
        if self._keyframe_request is None:
            return
        threading.Thread(target=self._safe_keyframe, daemon=True).start()

    def _safe_keyframe(self):
        try:
            self._keyframe_request()
        except Exception as e:
            logger.info("Keyframe request failed for camera %s: %s", self.camera_id, e)

    def _device_sends_nothing(self) -> bool:
        if not str(self.rtsp_url).startswith("rtsp"):
            return False
        try:
            from app.protocols.rtsp_diag import media_probe
            r = media_probe(self.rtsp_url, seconds=4)
            return not r["error"] and r["packets"] == 0
        except Exception:
            return False

    def _find_address(self, closed_msg: str):
        """The device hung up: try other address forms / login-first."""
        try:
            from app.protocols.rtsp_diag import CLOSED, find_working_url
            url, d, _tried = find_working_url(self.rtsp_url, timeout=3)
        except Exception:
            logger.exception("Address search failed")
            return "closed", closed_msg
        if url and url != self.rtsp_url:
            logger.warning("Camera %s answers on another address form", self.camera_id)
            self.rtsp_url = url
            self.url_learned.emit(self.camera_id, url)
        if d["kind"] == CLOSED:
            return "closed", d["message"] or closed_msg
        return d["kind"], d["message"]

    def _precheck(self):
        if not str(self.rtsp_url).startswith("rtsp"):
            return "", ""
        try:
            from app.protocols.rtsp_diag import describe
            return describe(self.rtsp_url, timeout=3)
        except Exception:
            return "", ""

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
        self.learned_backend: Dict[int, str] = {}
        self.learned_url: Dict[int, tuple] = {}   # cid -> (configured url, working url)
        from app.config import Config
        for key, v in dict(getattr(Config, "LEARNED_STREAMS", {}) or {}).items():
            try:
                cid = int(key)
            except ValueError:
                continue
            if v.get("transport") in ("tcp", "udp"):
                self.learned_transport[cid] = v["transport"]
            if v.get("backend") in BACKENDS:
                self.learned_backend[cid] = v["backend"]
        self.reconnect_delay = 2

    def add_frame_listener(self, cb: Callable):
        if cb not in self.frame_listeners:
            self.frame_listeners.append(cb)

    def start_stream(self, camera_id: int, rtsp_url: str,
                     on_frame=None, on_status=None, transport: str = "auto",
                     on_transport=None, keyframe_request=None,
                     snapshot=None) -> StreamThread:
        self.stop_stream(camera_id)
        learned = self.learned_url.get(camera_id)
        configured = rtsp_url
        if learned and learned[0] == rtsp_url:
            rtsp_url = learned[1]            # the address form found to work earlier
        thread = StreamThread(camera_id, rtsp_url, self.frame_listeners,
                              self.reconnect_delay, transport,
                              self.learned_transport.get(camera_id, "tcp"),
                              self.learned_backend.get(camera_id, preferred_decoder()),
                              keyframe_request, snapshot)
        thread.transport_learned.connect(lambda cid, t: self._learn(cid, transport=t))
        thread.backend_learned.connect(lambda cid, b: self._learn(cid, backend=b))
        thread.url_learned.connect(
            lambda cid, u, c=configured: self.learned_url.__setitem__(cid, (c, u)))
        if on_transport:
            thread.transport_learned.connect(on_transport)
        if on_frame:
            thread.frame_ready.connect(on_frame)
        if on_status:
            thread.status_changed.connect(on_status)
        self._streams[camera_id] = thread
        thread.start()
        return thread

    def _learn(self, cid: int, transport: str = None, backend: str = None):
        """Remember what worked, also for the next start of the app, so the
        camera connects at once instead of trying every decoder again."""
        from app.config import Config
        changed = False
        if transport and self.learned_transport.get(cid) != transport:
            self.learned_transport[cid] = transport
            changed = True
        if backend and self.learned_backend.get(cid) != backend:
            self.learned_backend[cid] = backend
            changed = True
        if not changed:
            return
        store = dict(getattr(Config, "LEARNED_STREAMS", {}) or {})
        entry = dict(store.get(str(cid), {}))
        if cid in self.learned_transport:
            entry["transport"] = self.learned_transport[cid]
        if cid in self.learned_backend:
            entry["backend"] = self.learned_backend[cid]
        store[str(cid)] = entry
        Config.LEARNED_STREAMS = store
        try:
            Config.save_settings()
        except Exception as e:
            logger.info("Could not save learned stream settings: %s", e)

    def stop_stream(self, camera_id: int):
        thread = self._streams.pop(camera_id, None)
        if thread is None:
            return
        # Never block the UI waiting for a read that may take READ_TIMEOUT_MS;
        # keep a reference until the thread exits so Qt doesn't destroy it early.
        thread.request_stop()
        for sig in (thread.frame_ready, thread.status_changed, thread.transport_learned,
                    thread.backend_learned, thread.url_learned):
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
