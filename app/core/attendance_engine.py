import calendar
import logging
import os
import threading
from datetime import date, datetime, time as dtime, timedelta
from typing import Dict, List, Optional

import cv2

from app.config import Config

logger = logging.getLogger(__name__)

STATUS_PRESENT, STATUS_LATE, STATUS_ABSENT = "Present", "Late", "Absent"


def shift_start_time() -> dtime:
    try:
        hh, mm = str(Config.SHIFT_START).split(":")[:2]
        return dtime(int(hh), int(mm))
    except Exception:
        return dtime(9, 0)


def is_late(check_in: datetime) -> bool:
    limit = datetime.combine(check_in.date(), shift_start_time()) + \
        timedelta(minutes=int(Config.LATE_GRACE_MINUTES))
    return check_in > limit


def is_weekly_off(d: date) -> bool:
    return d.weekday() in set(Config.WEEKLY_OFF_DAYS)


class AttendanceEngine:
    """Turns recognised faces into one attendance record per person per day.

    check_in  = first time the person is seen that day
    check_out = last time the person is seen that day
    """

    def __init__(self, db):
        self.db = db
        self._last_write: Dict[int, datetime] = {}
        self._lock = threading.Lock()

    def process(self, camera_id: int, frame, faces: List[Dict],
                now: Optional[datetime] = None) -> List[Dict]:
        now = now or datetime.now()
        events = []
        for face in faces:
            pid = face.get("person_id")
            if not pid:
                continue
            with self._lock:
                last = self._last_write.get(pid)
                if last and last.date() == now.date() and \
                        (now - last).total_seconds() < Config.ATTENDANCE_MIN_UPDATE_SECS:
                    continue
                self._last_write[pid] = now
            event = self._record(pid, camera_id, face.get("confidence", 0.0), frame, now)
            if event:
                event["name"] = face.get("name", "")
                events.append(event)
        return events

    def _record(self, person_id: int, camera_id: int, confidence: float,
                frame, now: datetime) -> Optional[Dict]:
        from app.models import AttendanceRecord
        today = now.date().isoformat()
        session = self.db.get_session()
        try:
            rec = (session.query(AttendanceRecord)
                   .filter_by(person_id=person_id, date=today)
                   .order_by(AttendanceRecord.id).first())
            if rec is None:
                snap = self._save_snapshot(frame, person_id, camera_id, now)
                session.add(AttendanceRecord(
                    person_id=person_id, camera_id=camera_id, check_in=now,
                    date=today, confidence=confidence, snapshot_path=snap))
                session.commit()
                return {"type": "check_in", "person_id": person_id,
                        "camera_id": camera_id, "time": now,
                        "late": is_late(now)}
            rec.check_out = now
            rec.confidence = max(rec.confidence or 0.0, confidence)
            session.commit()
            return {"type": "seen", "person_id": person_id,
                    "camera_id": camera_id, "time": now}
        except Exception:
            logger.exception("Attendance record failed")
            session.rollback()
            return None
        finally:
            session.close()

    @staticmethod
    def _save_snapshot(frame, person_id: int, camera_id: int, ts: datetime) -> str:
        if frame is None:
            return ""
        os.makedirs(Config.SNAPSHOTS_DIR, exist_ok=True)
        name = f"att_{person_id}_{camera_id}_{ts.strftime('%Y%m%d_%H%M%S')}.jpg"
        path = os.path.join(Config.SNAPSHOTS_DIR, name)
        cv2.imwrite(path, frame)
        return path

    # ---------------------------------------------------------------- reports
    @staticmethod
    def get_report(session, date_str: str = None, include_absent: bool = True) -> List[Dict]:
        from app.models import AttendanceRecord, Person
        date_str = date_str or date.today().isoformat()
        day = date.fromisoformat(date_str)
        records = {r.person_id: r for r in
                   session.query(AttendanceRecord).filter(AttendanceRecord.date == date_str)
                   .order_by(AttendanceRecord.check_in.desc())}
        persons = session.query(Person).order_by(Person.name).all()
        rows = []
        for p in persons:
            rec = records.get(p.id)
            if rec is None and (not include_absent or not p.is_active
                                or day > date.today() or is_weekly_off(day)):
                continue
            row = {
                "person_id": p.id, "name": p.name,
                "employee_id": p.employee_id or "", "department": p.department or "",
                "check_in": "--", "check_out": "--", "hours": "--",
                "confidence": "--", "status": STATUS_ABSENT,
            }
            if rec is not None:
                row["check_in"] = rec.check_in.strftime("%H:%M:%S") if rec.check_in else "--"
                row["check_out"] = rec.check_out.strftime("%H:%M:%S") if rec.check_out else "--"
                if rec.check_in and rec.check_out:
                    secs = (rec.check_out - rec.check_in).total_seconds()
                    row["hours"] = f"{secs / 3600:.2f}"
                row["confidence"] = f"{rec.confidence:.0%}" if rec.confidence else "--"
                row["status"] = STATUS_LATE if rec.check_in and is_late(rec.check_in) \
                    else STATUS_PRESENT
            rows.append(row)
        order = {STATUS_LATE: 0, STATUS_PRESENT: 0, STATUS_ABSENT: 1}
        rows.sort(key=lambda r: (order[r["status"]], r["check_in"], r["name"]))
        return rows

    @staticmethod
    def get_monthly_report(session, year: int, month: int):
        """Return (day_numbers, rows). Each row has per-day codes:
        P present, L late, A absent, O weekly off, '' future."""
        from app.models import AttendanceRecord, Person
        days_in_month = calendar.monthrange(year, month)[1]
        first, last = date(year, month, 1), date(year, month, days_in_month)
        recs = session.query(AttendanceRecord).filter(
            AttendanceRecord.date >= first.isoformat(),
            AttendanceRecord.date <= last.isoformat()).all()
        by_person: Dict[int, Dict[str, object]] = {}
        for r in recs:
            by_person.setdefault(r.person_id, {})[r.date] = r
        today = date.today()
        rows = []
        for p in session.query(Person).order_by(Person.name):
            person_recs = by_person.get(p.id, {})
            if not p.is_active and not person_recs:
                continue
            codes, present, late, absent, hours = [], 0, 0, 0, 0.0
            for d in range(1, days_in_month + 1):
                day = date(year, month, d)
                rec = person_recs.get(day.isoformat())
                if rec is not None:
                    if rec.check_in and is_late(rec.check_in):
                        codes.append("L"); late += 1
                    else:
                        codes.append("P")
                    present += 1
                    if rec.check_in and rec.check_out:
                        hours += (rec.check_out - rec.check_in).total_seconds() / 3600
                elif day > today:
                    codes.append("")
                elif is_weekly_off(day):
                    codes.append("O")
                else:
                    codes.append("A"); absent += 1
            rows.append({
                "name": p.name, "employee_id": p.employee_id or "",
                "department": p.department or "", "present": present,
                "late": late, "absent": absent, "hours": round(hours, 2),
                "days": codes,
            })
        return list(range(1, days_in_month + 1)), rows
