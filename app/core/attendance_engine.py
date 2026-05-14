import os
import cv2
import logging
from datetime import datetime, date
from typing import List, Dict
from app.core.face_engine import FaceEngine
from app.config import Config

logger = logging.getLogger(__name__)


class AttendanceEngine:
    def __init__(self, db, face_engine: FaceEngine):
        self.db = db
        self.face_engine = face_engine
        self._cooldown: Dict[int, datetime] = {}  # person_id -> last event time
        self.COOLDOWN_SECS = 60

    def process_frame(self, camera_id: int,
                      frame) -> List[Dict]:
        faces = self.face_engine.recognize_faces(
            frame, Config.FACE_RECOGNITION_THRESHOLD
        )
        events = []
        for face in faces:
            pid = face.get("person_id")
            if not pid:
                continue
            now = datetime.now()
            last = self._cooldown.get(pid)
            if last and (now - last).seconds < self.COOLDOWN_SECS:
                continue
            self._cooldown[pid] = now
            event = self._record(pid, camera_id, face["confidence"], frame, now)
            if event:
                event["name"] = face["name"]
                events.append(event)
        return events

    def _record(self, person_id: int, camera_id: int,
                confidence: float, frame, now: datetime) -> Dict:
        from app.models import AttendanceRecord
        today = now.date().isoformat()
        session = self.db.get_session()
        try:
            existing = (
                session.query(AttendanceRecord)
                .filter_by(person_id=person_id, date=today, camera_id=camera_id)
                .first()
            )
            snap = self._save_snapshot(frame, person_id, camera_id, now)
            if not existing:
                rec = AttendanceRecord(
                    person_id=person_id, camera_id=camera_id,
                    check_in=now, date=today,
                    confidence=confidence, snapshot_path=snap
                )
                session.add(rec)
                session.commit()
                return {"type": "check_in", "person_id": person_id, "time": now}
            elif not existing.check_out:
                existing.check_out = now
                session.commit()
                return {"type": "check_out", "person_id": person_id, "time": now}
        except Exception as e:
            logger.error("Attendance record: %s", e)
            session.rollback()
        finally:
            session.close()
        return {}

    def _save_snapshot(self, frame, person_id: int,
                       camera_id: int, ts: datetime) -> str:
        folder = Config.SNAPSHOTS_DIR
        os.makedirs(folder, exist_ok=True)
        name = f"att_{person_id}_{camera_id}_{ts.strftime('%Y%m%d_%H%M%S')}.jpg"
        path = os.path.join(folder, name)
        cv2.imwrite(path, frame)
        return path

    def get_report(self, session, date_str: str = None) -> List[Dict]:
        from app.models import AttendanceRecord, Person
        if not date_str:
            date_str = date.today().isoformat()
        rows = (
            session.query(AttendanceRecord, Person)
            .join(Person)
            .filter(AttendanceRecord.date == date_str)
            .order_by(AttendanceRecord.check_in)
            .all()
        )
        result = []
        for rec, person in rows:
            result.append({
                "id":          rec.id,
                "person_id":   person.id,
                "name":        person.name,
                "employee_id": person.employee_id or "",
                "department":  person.department or "",
                "check_in":    rec.check_in.strftime("%H:%M:%S") if rec.check_in else "--",
                "check_out":   rec.check_out.strftime("%H:%M:%S") if rec.check_out else "--",
                "confidence":  f"{rec.confidence:.0%}" if rec.confidence else "--",
            })
        return result
