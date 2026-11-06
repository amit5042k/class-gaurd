"""Person tracking: every time AI face recognition identifies an enrolled
student or staff member on a camera, a sighting is stored. Searching a name
gives where they are now, where they were last seen, and their movement."""
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import cv2

from app.config import Config

logger = logging.getLogger(__name__)

SIGHTING_GAP_S = 20        # one stored sighting per person and camera per this many seconds
VISIT_GAP_S = 120          # sightings further apart than this start a new visit
NOW_WINDOW_S = 60          # seen this recently = "visible now"


class TrackingService:
    def __init__(self, db):
        self.db = db
        self._last: Dict[tuple, float] = {}   # (person_id, camera_id) -> monotonic time

    # ---------------------------------------------------------------- write
    def record(self, camera_id: int, frame, faces: List[Dict],
               now: Optional[float] = None) -> List[Dict]:
        """Store sightings of recognised faces (throttled). Returns the new ones."""
        from app.models import Sighting
        now = time.monotonic() if now is None else now
        new = []
        for f in faces:
            pid = f.get("person_id")
            if pid is None:
                continue
            key = (pid, camera_id)
            if now - self._last.get(key, -1e9) < SIGHTING_GAP_S:
                continue
            self._last[key] = now
            new.append(f)
        if not new:
            return []
        out = []
        session = self.db.get_session()
        try:
            for f in new:
                when = datetime.now()
                snap = self._save_face(frame, f, camera_id, when)
                s = Sighting(person_id=f["person_id"], camera_id=camera_id, seen_at=when,
                             confidence=float(f.get("confidence") or 0.0), snapshot_path=snap)
                session.add(s)
                out.append({"person_id": f["person_id"], "name": f.get("name", ""),
                            "camera_id": camera_id, "time": when,
                            "confidence": s.confidence, "snapshot": snap})
            session.commit()
        except Exception:
            logger.exception("Could not save sightings")
            session.rollback()
            return []
        finally:
            session.close()
        return out

    @staticmethod
    def _save_face(frame, face, camera_id, when) -> Optional[str]:
        if frame is None or not face.get("bbox"):
            return None
        try:
            x1, y1, x2, y2 = face["bbox"]
            h, w = frame.shape[:2]
            pad = int(0.4 * max(1, y2 - y1))
            crop = frame[max(0, y1 - pad):min(h, y2 + pad), max(0, x1 - pad):min(w, x2 + pad)]
            if crop.size == 0:
                return None
            folder = os.path.join(Config.SNAPSHOTS_DIR, "sightings", f"{when:%Y%m%d}")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"p{face['person_id']}_c{camera_id}_{when:%H%M%S}.jpg")
            cv2.imwrite(path, crop)
            return path
        except Exception:
            logger.exception("Could not save sighting picture")
            return None

    # ----------------------------------------------------------------- read
    def find_people(self, text: str, limit: int = 20) -> List[Dict]:
        from app.models import Person
        text = (text or "").strip()
        session = self.db.get_session()
        try:
            q = session.query(Person).filter(Person.is_active.is_(True))
            if text:
                like = f"%{text}%"
                q = q.filter(Person.name.ilike(like) | Person.employee_id.ilike(like))
            return [{"id": p.id, "name": p.name, "employee_id": p.employee_id or "",
                     "department": p.department or "", "role": p.role or "",
                     "photo": p.photo_path or ""}
                    for p in q.order_by(Person.name).limit(limit)]
        finally:
            session.close()

    def sightings(self, person_id: int, since: datetime,
                  until: Optional[datetime] = None) -> List[Dict]:
        from app.models import Sighting
        session = self.db.get_session()
        try:
            q = session.query(Sighting).filter(Sighting.person_id == person_id,
                                               Sighting.seen_at >= since)
            if until is not None:
                q = q.filter(Sighting.seen_at <= until)
            return [{"camera_id": s.camera_id, "time": s.seen_at,
                     "confidence": s.confidence or 0.0, "snapshot": s.snapshot_path}
                    for s in q.order_by(Sighting.seen_at)]
        finally:
            session.close()

    def last_seen(self, person_id: int) -> Optional[Dict]:
        from app.models import Sighting
        session = self.db.get_session()
        try:
            s = (session.query(Sighting).filter(Sighting.person_id == person_id)
                 .order_by(Sighting.seen_at.desc()).first())
            return {"camera_id": s.camera_id, "time": s.seen_at,
                    "confidence": s.confidence or 0.0, "snapshot": s.snapshot_path} if s else None
        finally:
            session.close()

    @staticmethod
    def visits(sightings: List[Dict]) -> List[Dict]:
        """Group sightings into stays: same camera, no gap above VISIT_GAP_S."""
        out: List[Dict] = []
        for s in sightings:
            last = out[-1] if out else None
            if last and last["camera_id"] == s["camera_id"] and \
                    (s["time"] - last["end"]).total_seconds() <= VISIT_GAP_S:
                last["end"] = s["time"]
                last["count"] += 1
            else:
                out.append({"camera_id": s["camera_id"], "start": s["time"],
                            "end": s["time"], "count": 1, "snapshot": s["snapshot"]})
        return out

    @staticmethod
    def is_now(when: datetime, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now()
        return now - when <= timedelta(seconds=NOW_WINDOW_S)


def purge_old_sightings(db, days: int = None) -> int:
    """Delete sightings (and their pictures) older than the retention period."""
    from app.models import Sighting
    days = int(days if days is not None else Config.TRACKING_RETENTION_DAYS)
    if days <= 0:
        return 0
    cutoff = datetime.now() - timedelta(days=days)
    session = db.get_session()
    try:
        old = session.query(Sighting).filter(Sighting.seen_at < cutoff).all()
        for s in old:
            if s.snapshot_path and os.path.exists(s.snapshot_path):
                try:
                    os.remove(s.snapshot_path)
                except OSError:
                    pass
            session.delete(s)
        session.commit()
        return len(old)
    except Exception:
        logger.exception("Could not purge old sightings")
        session.rollback()
        return 0
    finally:
        session.close()
