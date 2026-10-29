import logging
import os
import threading
import time
from datetime import datetime
from typing import Dict, Optional, Tuple

import cv2

from app.config import Config

logger = logging.getLogger(__name__)


class AlertService:
    """Creates Alert rows (plus an optional SafetyViolation) with a per-key
    cooldown, so a person standing in a zone creates one alert, not 25 a second."""

    def __init__(self, db):
        self.db = db
        self._last: Dict[Tuple, float] = {}
        self._lock = threading.Lock()

    def _allowed(self, key: Tuple) -> bool:
        now = time.monotonic()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < float(Config.ALERT_COOLDOWN):
                return False
            self._last[key] = now
            return True

    def raise_alert(self, camera_id: Optional[int], alert_type: str, description: str,
                    severity: str = "medium", frame=None, key: Tuple = None,
                    violation_type: str = None) -> Optional[Dict]:
        if not self._allowed(key or (camera_id, alert_type)):
            return None
        from app.models import Alert, SafetyViolation
        snap = self._save_snapshot(frame, camera_id, alert_type)
        session = self.db.get_session()
        try:
            alert = Alert(camera_id=camera_id, alert_type=alert_type,
                          description=description, severity=severity,
                          snapshot_path=snap)
            session.add(alert)
            if violation_type:
                session.add(SafetyViolation(camera_id=camera_id,
                                            violation_type=violation_type,
                                            description=description,
                                            snapshot_path=snap))
            session.commit()
            return {"id": alert.id, "camera_id": camera_id, "type": alert_type,
                    "description": description, "severity": severity,
                    "snapshot": snap, "time": alert.created_at}
        except Exception:
            logger.exception("Could not save alert")
            session.rollback()
            return None
        finally:
            session.close()

    @staticmethod
    def _save_snapshot(frame, camera_id, alert_type) -> str:
        if frame is None:
            return ""
        os.makedirs(Config.SNAPSHOTS_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_type = "".join(c if c.isalnum() else "_" for c in alert_type)
        path = os.path.join(Config.SNAPSHOTS_DIR, f"alert_{camera_id}_{safe_type}_{ts}.jpg")
        cv2.imwrite(path, frame)
        return path
