"""Video recording: continuous, motion-triggered and manual, in time segments.

Each camera has its own writer thread fed through a small queue, so encoding
never slows down the stream thread. Files are indexed in the `recordings`
table so the UI can draw a day timeline and jump from an alert to its video.
"""
import collections
import logging
import os
import queue
import shutil
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import cv2
import numpy as np

from app.config import Config

logger = logging.getLogger(__name__)

MODE_OFF, MODE_CONTINUOUS, MODE_MOTION = "off", "continuous", "motion"
KIND_CONTINUOUS, KIND_MOTION, KIND_MANUAL = "continuous", "motion", "manual"
MOTION_LEVELS = {"low": 0.03, "medium": 0.01, "high": 0.003}
PRE_ROLL_FPS = 5


def camera_dir(camera_id: int, day: datetime) -> str:
    return os.path.join(Config.RECORDINGS_DIR, f"cam{camera_id}", day.strftime("%Y-%m-%d"))


class MotionDetector:
    """Cheap frame-difference motion detector on a small greyscale copy."""

    def __init__(self):
        self._prev = None

    def update(self, frame: np.ndarray) -> float:
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (320, max(1, int(h * 320 / w))), interpolation=cv2.INTER_AREA)
        gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        prev, self._prev = self._prev, gray
        if prev is None or prev.shape != gray.shape:
            return 0.0
        diff = cv2.absdiff(prev, gray)
        return float(np.count_nonzero(diff > 25)) / diff.size


class CameraRecorder(threading.Thread):
    def __init__(self, db, camera_id: int, on_state: Callable[[int, bool], None]):
        super().__init__(daemon=True, name=f"rec-{camera_id}")
        self.db = db
        self.camera_id = camera_id
        self._on_state = on_state
        self._queue: "queue.Queue" = queue.Queue(maxsize=60)
        self._running = True
        self.mode = MODE_OFF
        self.manual = False
        self._writer: Optional[cv2.VideoWriter] = None
        self._path = ""
        self._kind = ""
        self._rec_id: Optional[int] = None
        self._seg_start = 0.0
        self._written = 0
        self._fps = 15.0
        self._size = None
        self._stamps = collections.deque(maxlen=30)
        self._preroll = collections.deque()
        self._last_preroll = 0.0
        self._motion = MotionDetector()
        self._last_motion = 0.0
        self._last_frame_time = 0.0
        self.dropped = 0

    # ------------------------------------------------------ called by others
    def submit(self, frame: np.ndarray):
        try:
            self._queue.put_nowait((time.time(), frame))
        except queue.Full:
            self.dropped += 1  # encoder can't keep up; skip rather than lag

    def stop(self):
        self._running = False
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    @property
    def recording(self) -> bool:
        return self._writer is not None

    # -------------------------------------------------------------- thread
    def run(self):
        while self._running:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                self._idle_check()
                continue
            if item is None:
                break
            ts, frame = item
            try:
                self._handle(ts, frame)
            except Exception:
                logger.exception("Recording failed on camera %s", self.camera_id)
                self._close()
        self._close()

    def _idle_check(self):
        # Stream stopped: close the file instead of leaving it open forever.
        if self._writer is not None and time.time() - self._last_frame_time > 5:
            self._close()

    def _wanted_kind(self, ts: float, frame) -> Optional[str]:
        if self.manual:
            return KIND_MANUAL
        if self.mode == MODE_CONTINUOUS:
            return KIND_CONTINUOUS
        if self.mode == MODE_MOTION:
            level = MOTION_LEVELS.get(Config.MOTION_SENSITIVITY, MOTION_LEVELS["medium"])
            if self._motion.update(frame) >= level:
                self._last_motion = ts
            if ts - self._last_motion <= float(Config.MOTION_POST_ROLL):
                return KIND_MOTION
        return None

    def _handle(self, ts: float, frame: np.ndarray):
        self._last_frame_time = ts
        self._stamps.append(ts)
        frame = self._resize(frame)
        kind = self._wanted_kind(ts, frame)

        if kind is None:
            if self._writer is not None:
                self._close()
            if self.mode == MODE_MOTION and ts - self._last_preroll >= 1 / PRE_ROLL_FPS:
                self._last_preroll = ts
                self._preroll.append((ts, frame))
                while self._preroll and ts - self._preroll[0][0] > float(Config.MOTION_PRE_ROLL):
                    self._preroll.popleft()
            return

        segment = float(Config.RECORDING_SEGMENT_MINUTES) * 60
        if self._writer is not None and (kind != self._kind or ts - self._seg_start >= segment
                                         or frame.shape[:2] != self._size):
            self._close()
        if self._writer is None:
            self._open(kind, ts, frame)
        self._write_timed(ts, frame)

    def _resize(self, frame):
        max_w = int(Config.RECORDING_MAX_WIDTH or 0)
        if max_w and frame.shape[1] > max_w:
            h = int(frame.shape[0] * max_w / frame.shape[1]) // 2 * 2
            frame = cv2.resize(frame, (max_w, h), interpolation=cv2.INTER_AREA)
        return frame

    def _measured_fps(self) -> float:
        if len(self._stamps) < 5:
            return 15.0
        span = self._stamps[-1] - self._stamps[0]
        fps = (len(self._stamps) - 1) / span if span > 0 else 15.0
        return float(min(30, max(2, round(fps))))

    def _open(self, kind: str, ts: float, frame):
        start = datetime.fromtimestamp(ts)
        preroll = list(self._preroll) if kind == KIND_MOTION else []
        self._preroll.clear()
        if preroll:
            start = datetime.fromtimestamp(preroll[0][0])
        folder = camera_dir(self.camera_id, start)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"cam{self.camera_id}_{start:%Y%m%d_%H%M%S}_{kind}.mp4")
        h, w = frame.shape[:2]
        fps = self._measured_fps()
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        if not writer.isOpened():
            logger.error("Could not open video file %s", path)
            return
        self._writer, self._path, self._kind = writer, path, kind
        self._fps, self._size, self._written = fps, (h, w), 0
        self._seg_start = preroll[0][0] if preroll else ts
        self._rec_id = self._db_insert(path, kind, start)
        for pts, pframe in preroll:
            if pframe.shape[:2] == (h, w):
                self._write_timed(pts, pframe)
        logger.info("Recording %s (%s, %.0f fps)", path, kind, fps)
        self._on_state(self.camera_id, True)

    def _write_timed(self, ts: float, frame):
        # Keep the file in real time: duplicate frames when the camera
        # delivers fewer than fps, skip when it delivers more.
        if self._writer is None:
            return
        due = int((ts - self._seg_start) * self._fps) + 1
        n = min(due - self._written, int(self._fps) * 2)
        for _ in range(max(0, n)):
            self._writer.write(frame)
        self._written += max(0, n)

    def _close(self):
        if self._writer is None:
            return
        self._writer.release()
        end = datetime.fromtimestamp(self._seg_start + self._written / self._fps)
        size = os.path.getsize(self._path) if os.path.exists(self._path) else 0
        if self._written < 2:
            try:
                os.remove(self._path)
            except OSError:
                pass
            self._db_delete(self._rec_id)
        else:
            self._db_finish(self._rec_id, end, size)
        self._writer, self._rec_id = None, None
        self._on_state(self.camera_id, False)

    # ------------------------------------------------------------------ DB
    def _db_insert(self, path, kind, start) -> Optional[int]:
        from app.models import Recording
        s = self.db.get_session()
        try:
            rec = Recording(camera_id=self.camera_id, path=path, kind=kind, start_time=start)
            s.add(rec)
            s.commit()
            return rec.id
        except Exception:
            logger.exception("Could not index recording")
            s.rollback()
            return None
        finally:
            s.close()

    def _db_finish(self, rec_id, end, size):
        if rec_id is None:
            return
        from app.models import Recording
        s = self.db.get_session()
        try:
            rec = s.get(Recording, rec_id)
            if rec:
                rec.end_time, rec.size_bytes = end, size
                s.commit()
        finally:
            s.close()

    def _db_delete(self, rec_id):
        if rec_id is None:
            return
        from app.models import Recording
        s = self.db.get_session()
        try:
            s.query(Recording).filter_by(id=rec_id).delete()
            s.commit()
        finally:
            s.close()


class RecordingManager:
    """Owns the per-camera recorders and the disk clean-up."""

    def __init__(self, db, on_state: Callable[[int, bool], None] = None):
        self.db = db
        self._on_state = on_state or (lambda cid, rec: None)
        self._recorders: Dict[int, CameraRecorder] = {}
        self._lock = threading.Lock()
        self._modes: Dict[int, str] = {}
        self._cleanup_stop = threading.Event()
        self._cleanup_thread: Optional[threading.Thread] = None

    def start_cleanup(self, interval_s: int = 600, first_delay_s: int = 30):
        def loop():
            if self._cleanup_stop.wait(first_delay_s):
                return
            while True:
                try:
                    self.enforce_retention()
                except Exception:
                    logger.exception("Recording clean-up failed")
                if self._cleanup_stop.wait(interval_s):
                    return
        self._cleanup_thread = threading.Thread(target=loop, daemon=True, name="rec-cleanup")
        self._cleanup_thread.start()

    def _get(self, camera_id: int) -> CameraRecorder:
        with self._lock:
            rec = self._recorders.get(camera_id)
            if rec is None or not rec.is_alive():
                rec = CameraRecorder(self.db, camera_id, self._on_state)
                rec.mode = self._modes.get(camera_id, MODE_OFF)
                self._recorders[camera_id] = rec
                rec.start()
            return rec

    def set_cameras(self, cameras: List[Dict]):
        modes = {c["id"]: c.get("recording_mode") or MODE_OFF for c in cameras}
        with self._lock:
            self._modes = modes
            gone = [cid for cid in self._recorders if cid not in modes]
            for cid, rec in self._recorders.items():
                rec.mode = modes.get(cid, MODE_OFF)
        for cid in gone:
            self.remove_camera(cid)

    def remove_camera(self, camera_id: int):
        with self._lock:
            rec = self._recorders.pop(camera_id, None)
        if rec:
            rec.stop()

    def set_manual(self, camera_id: int, on: bool):
        self._get(camera_id).manual = on

    def is_manual(self, camera_id: int) -> bool:
        rec = self._recorders.get(camera_id)
        return bool(rec and rec.manual)

    def is_recording(self, camera_id: int) -> bool:
        rec = self._recorders.get(camera_id)
        return bool(rec and rec.recording)

    def on_frame(self, camera_id: int, frame: np.ndarray):
        """Frame listener (runs on stream threads)."""
        mode = self._modes.get(camera_id, MODE_OFF)
        rec = self._recorders.get(camera_id)
        if mode == MODE_OFF and not (rec and rec.manual):
            if rec and rec.recording:
                rec.submit(frame)  # lets it notice and close cleanly
            return
        self._get(camera_id).submit(frame)

    def stop_all(self, wait_s: float = 5):
        self._cleanup_stop.set()
        with self._lock:
            recs = list(self._recorders.values())
            self._recorders.clear()
        for r in recs:
            r.stop()
        deadline = time.time() + wait_s
        for r in recs:
            r.join(max(0, deadline - time.time()))

    # ------------------------------------------------------------ clean-up
    def open_paths(self) -> set:
        return {r._path for r in list(self._recorders.values()) if r.recording}

    def enforce_retention(self) -> int:
        """Delete old footage. Returns the number of files removed."""
        from app.models import Recording
        removed = 0
        busy = self.open_paths()
        s = self.db.get_session()
        try:
            recs = s.query(Recording).order_by(Recording.start_time).all()
            cutoff = datetime.now() - timedelta(days=int(Config.RECORDING_RETENTION_DAYS))
            max_bytes = float(Config.RECORDING_MAX_GB) * 1024 ** 3
            total = sum((r.size_bytes or _size(r.path)) for r in recs)
            min_free = float(Config.RECORDING_MIN_FREE_GB) * 1024 ** 3
            for r in recs:
                if r.path in busy:
                    continue
                too_old = r.start_time and r.start_time < cutoff
                too_big = max_bytes > 0 and total > max_bytes
                low_disk = _free_bytes(Config.RECORDINGS_DIR) < min_free
                missing = not os.path.exists(r.path)
                if not (too_old or too_big or low_disk or missing):
                    continue
                size = r.size_bytes or _size(r.path)
                try:
                    if not missing:
                        os.remove(r.path)
                except OSError:
                    logger.warning("Could not delete %s", r.path)
                    continue
                total -= size
                s.delete(r)
                removed += 1
            s.commit()
        finally:
            s.close()
        _remove_empty_dirs(Config.RECORDINGS_DIR)
        if removed:
            logger.info("Recording clean-up removed %d file(s)", removed)
        return removed


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _free_bytes(path: str) -> float:
    try:
        os.makedirs(path, exist_ok=True)
        return shutil.disk_usage(path).free
    except OSError:
        return float("inf")


def _remove_empty_dirs(root: str):
    if not os.path.isdir(root):
        return
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if dirpath != root and not dirnames and not filenames:
            try:
                os.rmdir(dirpath)
            except OSError:
                pass


def recordings_for_day(db, camera_id: int, day) -> List[Dict]:
    from app.models import Recording
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    s = db.get_session()
    try:
        rows = (s.query(Recording)
                .filter(Recording.camera_id == camera_id,
                        Recording.start_time < end,
                        (Recording.end_time.is_(None)) | (Recording.end_time > start))
                .order_by(Recording.start_time).all())
        return [{"id": r.id, "path": r.path, "kind": r.kind, "start": r.start_time,
                 "end": r.end_time, "size": r.size_bytes or _size(r.path)} for r in rows]
    finally:
        s.close()


def find_recording_at(db, camera_id: int, when: datetime) -> Optional[Dict]:
    """The finished recording covering `when` (for 'play video' on an alert)."""
    from app.models import Recording
    s = db.get_session()
    try:
        r = (s.query(Recording)
             .filter(Recording.camera_id == camera_id, Recording.start_time <= when,
                     Recording.end_time.isnot(None), Recording.end_time >= when)
             .order_by(Recording.start_time.desc()).first())
        if r is None:
            return None
        return {"path": r.path, "start": r.start_time, "end": r.end_time,
                "offset": (when - r.start_time).total_seconds()}
    finally:
        s.close()
