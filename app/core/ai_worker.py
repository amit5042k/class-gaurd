import logging
import threading
import time
from typing import Dict, List, Optional

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from app.config import Config
from app.core.alerts import AlertService
from app.core.attendance_engine import AttendanceEngine
from app.core.face_engine import FaceEngine
from app.core.stream_manager import to_qimage

logger = logging.getLogger(__name__)

FACE, EXPRESSION, SAFETY = "face", "expression", "safety"


class AIWorker(QThread):
    """Runs every AI feature on one background thread.

    Stream threads hand over their newest frame via submit_frame(); older
    unprocessed frames are simply replaced, so a slow model never builds a
    backlog. Which analyses run for a camera is decided by its AI switches
    (Camera Management) or by a page currently monitoring it.
    """

    annotated_frame = pyqtSignal(str, int, QImage)   # kind, camera_id, image
    expression_result = pyqtSignal(int, list)
    safety_result = pyqtSignal(int, list)
    attendance_event = pyqtSignal(dict)
    alert_created = pyqtSignal(dict)
    model_status = pyqtSignal(str)

    def __init__(self, db, face_engine: FaceEngine, parent=None):
        super().__init__(parent)
        self.db = db
        self.face_engine = face_engine
        self.attendance = AttendanceEngine(db)
        self.alerts = AlertService(db)
        self._lock = threading.Lock()
        self._frames: Dict[int, tuple] = {}          # cid -> (frame, seq)
        self._seq = 0
        self._cameras: Dict[int, Dict] = {}
        self._zones: Dict[int, List[np.ndarray]] = {}
        self._monitor: Dict[str, Optional[int]] = {EXPRESSION: None, SAFETY: None}
        self._last_run: Dict[tuple, float] = {}      # (cid, task) -> time
        self._last_seq: Dict[tuple, int] = {}        # (cid, task) -> seq
        self._last_expr_log: Dict[int, float] = {}
        self._pending_model: Optional[dict] = None
        self._running = False
        self.expression_engine = None
        self.safety_engine = None

    # ------------------------------------------------ thread-safe controls
    def submit_frame(self, camera_id: int, frame: np.ndarray):
        with self._lock:
            self._seq += 1
            self._frames[camera_id] = (frame, self._seq)

    def clear_camera(self, camera_id: int):
        with self._lock:
            self._frames.pop(camera_id, None)

    def set_cameras(self, cameras: List[Dict]):
        with self._lock:
            self._cameras = {c["id"]: dict(c) for c in cameras}

    def set_monitor(self, kind: str, camera_id: Optional[int]):
        with self._lock:
            self._monitor[kind] = camera_id

    def set_zones(self, camera_id: int, zones: List[np.ndarray]):
        with self._lock:
            self._zones[camera_id] = [np.asarray(z, dtype=np.int32) for z in zones]

    def request_model(self, model: Optional[dict]):
        """model = {"weights", "names", "cfg"} or {} to unload."""
        with self._lock:
            self._pending_model = model or {}

    def stop(self, wait_ms: int = 5000):
        self._running = False
        self.wait(wait_ms)

    # ------------------------------------------------------------- the loop
    def run(self):
        from app.core.expression_engine import ExpressionEngine
        from app.core.safety_engine import SafetyEngine
        self.expression_engine = ExpressionEngine()
        self.safety_engine = SafetyEngine()
        self._running = True
        while self._running:
            self._apply_pending_model()
            worked = False
            with self._lock:
                frames = dict(self._frames)
                cameras = dict(self._cameras)
                monitor = dict(self._monitor)
            for cid, (frame, seq) in frames.items():
                if not self._running:
                    break
                cam = cameras.get(cid, {})
                tasks = {
                    FACE: bool(cam.get("face_detection") or cam.get("attendance_tracking")),
                    EXPRESSION: bool(cam.get("expression_monitor")) or monitor[EXPRESSION] == cid,
                    SAFETY: bool(cam.get("safety_watch")) or monitor[SAFETY] == cid,
                }
                for task, enabled in tasks.items():
                    if enabled and self._due(cid, task, seq):
                        try:
                            self._run_task(task, cid, cam, frame,
                                           monitored=monitor.get(task) == cid)
                        except Exception:
                            logger.exception("AI task %s failed on camera %s", task, cid)
                        worked = True
            if not worked:
                time.sleep(0.05)

    def _due(self, cid: int, task: str, seq: int) -> bool:
        key = (cid, task)
        if self._last_seq.get(key) == seq:
            return False
        interval = {FACE: Config.FACE_INTERVAL, EXPRESSION: Config.EXPRESSION_INTERVAL,
                    SAFETY: Config.SAFETY_INTERVAL}[task]
        now = time.monotonic()
        if now - self._last_run.get(key, 0) < interval:
            return False
        self._last_run[key] = now
        self._last_seq[key] = seq
        return True

    def _apply_pending_model(self):
        with self._lock:
            model, self._pending_model = self._pending_model, None
        if model is None:
            return
        if not model:
            self.safety_engine.unload_model()
            self.model_status.emit("No model loaded: using the built-in person detector.")
            return
        err = self.safety_engine.load_model(model.get("weights", ""),
                                            model.get("names", ""), model.get("cfg", ""))
        self.model_status.emit(err or "Model loaded.")

    def _run_task(self, task, cid, cam, frame, monitored):
        name = cam.get("name", f"Camera {cid}")
        if task == FACE:
            self._run_faces(cid, cam, name, frame)
        elif task == EXPRESSION:
            self._run_expression(cid, name, frame, monitored)
        elif task == SAFETY:
            self._run_safety(cid, name, frame, monitored)

    # ---------------------------------------------------------------- tasks
    def _run_faces(self, cid, cam, name, frame):
        faces = self.face_engine.recognize_faces(
            frame, Config.FACE_RECOGNITION_THRESHOLD, Config.FACE_DETECTION_SCALE)
        if cam.get("attendance_tracking"):
            for ev in self.attendance.process(cid, frame, faces):
                ev["camera_name"] = name
                self.attendance_event.emit(ev)
        if Config.ALERT_UNKNOWN_FACES and any(f["person_id"] is None for f in faces):
            annotated = self.face_engine.draw_faces(frame.copy(), faces)
            self._emit_alert(self.alerts.raise_alert(
                cid, "Unknown person", f"Unrecognised face at {name}", "low",
                annotated, key=(cid, "unknown_face")))

    def _run_expression(self, cid, name, frame, monitored):
        ee = self.expression_engine
        results = ee.analyze(frame)
        self.expression_result.emit(cid, results)
        annotated = None
        if monitored or results:
            annotated = ee.draw(frame.copy(), results)
        if monitored:
            self.annotated_frame.emit(EXPRESSION, cid, to_qimage(annotated))
        if not ee.available or not results:
            return
        now = time.monotonic()
        if now - self._last_expr_log.get(cid, 0) >= Config.EXPRESSION_LOG_INTERVAL:
            self._last_expr_log[cid] = now
            self._log_expressions(cid, results)
        for r in results:
            emo = r["dominant_emotion"]
            if emo in Config.ALERT_EXPRESSIONS and \
                    r["confidence"] >= float(Config.EXPRESSION_ALERT_CONFIDENCE):
                self._emit_alert(self.alerts.raise_alert(
                    cid, "Expression", f"{emo.capitalize()} expression detected at {name}",
                    "medium", annotated, key=(cid, "expr", emo)))

    def _log_expressions(self, cid, results):
        from app.models import ExpressionLog
        session = self.db.get_session()
        try:
            for r in results:
                session.add(ExpressionLog(camera_id=cid, expression=r["dominant_emotion"],
                                          confidence=r["confidence"]))
            session.commit()
        except Exception:
            logger.exception("Could not save expression log")
            session.rollback()
        finally:
            session.close()

    def _run_safety(self, cid, name, frame, monitored):
        with self._lock:
            zones = list(self._zones.get(cid, []))
        dets = self.safety_engine.detect(frame, zones)
        self.safety_result.emit(cid, dets)
        bad = [d for d in dets if d["violation"] or d["zone_intrusion"]]
        annotated = None
        if monitored or bad:
            annotated = self.safety_engine.draw(frame.copy(), dets, zones)
        if monitored:
            # Full resolution: zone points are drawn in source-frame pixels.
            self.annotated_frame.emit(SAFETY, cid, to_qimage(annotated))
        for d in bad:
            if d["zone_intrusion"]:
                self._emit_alert(self.alerts.raise_alert(
                    cid, "Zone intrusion", f"{d['class'].capitalize()} inside a restricted "
                    f"zone at {name}", "high", annotated, key=(cid, "zone"),
                    violation_type="zone_intrusion"))
            if d["violation"]:
                self._emit_alert(self.alerts.raise_alert(
                    cid, "PPE violation", f"{d['class']} detected at {name}", "high",
                    annotated, key=(cid, "ppe", d["class"]), violation_type=d["class"]))

    def _emit_alert(self, alert: Optional[Dict]):
        if alert:
            self.alert_created.emit(alert)
