"""Class mood alarm and person tracking."""
from datetime import datetime, timedelta

import numpy as np


def test_class_mood_levels_and_window(monkeypatch):
    from app.config import Config
    from app.core.class_mood import ALARM, GOOD, ClassMoodTracker
    monkeypatch.setattr(Config, "CLASS_MOOD_WINDOW", 60)
    monkeypatch.setattr(Config, "CLASS_MOOD_MIN_FACES", 8)
    monkeypatch.setattr(Config, "CLASS_MOOD_ALERT_PERCENT", 40.0)
    t = ClassMoodTracker()
    s = t.add(1, ["happy", "neutral", "sad"], now=0)
    assert not s["enough"] and s["level"] == GOOD           # too few faces to judge
    s = t.add(1, ["happy"] * 4 + ["neutral"] * 4, now=5)
    assert s["enough"] and s["level"] == GOOD and round(s["negative_pct"]) == 9
    s = t.add(1, ["angry", "sad", "fear", "sad", "angry", "disgust", "sad"], now=10)
    assert s["level"] == ALARM and t.alarm(s) and s["dominant_negative"] == "sad"
    s = t.add(1, ["happy"] * 10, now=100)                    # old faces left the window
    assert s["samples"] == 10 and s["level"] == GOOD
    assert t.add(2, [], now=100)["samples"] == 0             # cameras are separate


def test_ai_worker_raises_class_mood_alarm(db, tmp_path, monkeypatch):
    from app.config import Config
    from app.core.ai_worker import AIWorker
    from app.core.face_engine import FaceEngine
    monkeypatch.setattr(Config, "CLASS_MOOD_MIN_FACES", 3)
    monkeypatch.setattr(Config, "CLASS_MOOD_ALERT_PERCENT", 50.0)
    monkeypatch.setattr(Config, "SNAPSHOTS_DIR", str(tmp_path / "snaps"))

    class Expr:
        available = True

        def analyze(self, frame):
            return [{"dominant_emotion": "angry", "confidence": 30.0, "bbox": (0, 0, 9, 9)}] * 4

        def draw(self, frame, results):
            return frame

    w = AIWorker(db, FaceEngine(str(tmp_path / "faces")))
    w.expression_engine = Expr()
    moods, alerts = [], []
    w.class_mood.connect(lambda cid, m: moods.append(m))
    w.alert_created.connect(alerts.append)
    w._run_expression(7, "Class 5A", np.zeros((60, 80, 3), np.uint8), monitored=False)
    assert moods and moods[-1]["level"] == "Alarm"
    assert any(a["type"] == "Class mood" and "Class 5A" in a["description"] for a in alerts)


def _person(db, name, emp=None):
    from app.models import Person
    s = db.get_session()
    p = Person(name=name, employee_id=emp, role="Student", department="5A")
    s.add(p)
    s.commit()
    pid = p.id
    s.close()
    return pid


def test_tracking_records_throttled_sightings_and_visits(db, tmp_path, monkeypatch):
    from app.config import Config
    from app.core.tracking import TrackingService
    monkeypatch.setattr(Config, "SNAPSHOTS_DIR", str(tmp_path / "snaps"))
    riya = _person(db, "Riya Sharma", "S-101")
    _person(db, "Riyaan Gupta")
    t = TrackingService(db)
    frame = np.full((240, 320, 3), 90, np.uint8)
    face = {"person_id": riya, "name": "Riya Sharma", "confidence": 0.8,
            "bbox": (100, 60, 160, 130)}
    unknown = {"person_id": None, "name": "Unknown", "bbox": (0, 0, 10, 10)}
    assert len(t.record(1, frame, [face, unknown], now=0)) == 1
    assert t.record(1, frame, [face], now=5) == []          # throttled
    assert len(t.record(2, frame, [face], now=6)) == 1      # another camera
    assert len(t.record(1, frame, [face], now=30)) == 1
    seen = t.sightings(riya, datetime.now() - timedelta(hours=1))
    assert [s["camera_id"] for s in seen] == [1, 2, 1]
    assert seen[0]["snapshot"] and (tmp_path / "snaps" / "sightings").exists()
    assert t.last_seen(riya)["camera_id"] == 1
    assert [p["name"] for p in t.find_people("riya")] == ["Riya Sharma", "Riyaan Gupta"]
    assert [p["name"] for p in t.find_people("S-101")] == ["Riya Sharma"]

    base = datetime(2026, 1, 1, 9, 0)
    visits = t.visits([{"camera_id": 1, "time": base, "snapshot": None},
                       {"camera_id": 1, "time": base + timedelta(seconds=60), "snapshot": None},
                       {"camera_id": 2, "time": base + timedelta(seconds=90), "snapshot": None},
                       {"camera_id": 2, "time": base + timedelta(minutes=30), "snapshot": None}])
    assert [(v["camera_id"], v["count"]) for v in visits] == [(1, 2), (2, 1), (2, 1)]


def test_purge_old_sightings(db):
    from app.core.tracking import purge_old_sightings
    from app.models import Sighting
    pid = _person(db, "Old")
    s = db.get_session()
    s.add(Sighting(person_id=pid, camera_id=1, seen_at=datetime.now() - timedelta(days=40)))
    s.add(Sighting(person_id=pid, camera_id=1, seen_at=datetime.now()))
    s.commit()
    s.close()
    assert purge_old_sightings(db, days=30) == 1


def test_tracking_page_search_by_name(qtbot, db, tmp_path, monkeypatch):
    from app.config import Config
    from app.core.tracking import TrackingService
    from app.ui.pages.tracking import TrackingPage
    monkeypatch.setattr(Config, "SNAPSHOTS_DIR", str(tmp_path))
    riya = _person(db, "Riya Sharma")
    _person(db, "Aman Verma")
    TrackingService(db).record(3, np.zeros((100, 100, 3), np.uint8),
                               [{"person_id": riya, "name": "Riya Sharma",
                                 "confidence": 0.9, "bbox": (10, 10, 40, 40)}])
    page = TrackingPage(db)
    qtbot.addWidget(page)
    page.set_cameras([{"id": 3, "name": "Corridor", "location": "Block B"}])
    page.search.setText("riya sharma")
    page.track()
    assert page.name_lbl.text() == "Riya Sharma"
    assert "Visible now on Corridor (Block B)" in page.now_lbl.text()
    assert page.visits.rowCount() == 1 and page.visits.item(0, 2).text() == "Corridor"
    got = []
    page.live_requested.connect(got.append)
    page._show_live()
    assert got == [3]
    page.search.setText("a")                                   # several matches
    page.track()
    assert page.matches.count() == 2
    page.search.setText("nobody")
    page.track()
    assert "No enrolled person" in page.name_lbl.text()
