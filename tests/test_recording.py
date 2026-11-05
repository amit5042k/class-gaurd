import os
import time
from datetime import datetime, timedelta

import cv2
import numpy as np
import pytest

from app.config import Config
from app.core import recording as rec_mod
from app.core.recording import RecordingManager, find_recording_at, recordings_for_day


@pytest.fixture(autouse=True)
def rec_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "RECORDINGS_DIR", str(tmp_path / "rec"))
    monkeypatch.setattr(Config, "MOTION_POST_ROLL", 1)
    monkeypatch.setattr(Config, "MOTION_PRE_ROLL", 1)
    monkeypatch.setattr(Config, "RECORDING_MAX_WIDTH", 320)


def _feed(mgr, cid, seconds, fps=15, moving=False, start_x=0):
    for i in range(int(seconds * fps)):
        f = np.full((240, 320, 3), 40, np.uint8)
        x = (start_x + i * 6) % 280 if moving else 10
        cv2.rectangle(f, (x, 80), (x + 40, 160), (255, 255, 255), -1)
        mgr.on_frame(cid, f)
        time.sleep(1 / fps)


def _wait_closed(db, n, timeout=10):
    from app.models import Recording
    end = time.time() + timeout
    while time.time() < end:
        s = db.get_session()
        rows = s.query(Recording).filter(Recording.end_time.isnot(None)).all()
        s.close()
        if len(rows) >= n:
            return rows
        time.sleep(0.2)
    raise AssertionError("recording was not finalised")


def _duration(path):
    cap = cv2.VideoCapture(path)
    n, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return n / fps if fps else 0


def test_continuous_recording_is_playable_and_real_time(db):
    mgr = RecordingManager(db)
    mgr.set_cameras([{"id": 1, "recording_mode": "continuous"}])
    _feed(mgr, 1, 3)
    mgr.stop_all()
    rows = _wait_closed(db, 1)
    assert rows[0].kind == "continuous" and os.path.exists(rows[0].path)
    assert 2.0 <= _duration(rows[0].path) <= 4.0
    assert rows[0].size_bytes > 0
    assert recordings_for_day(db, 1, datetime.now().date())


def test_motion_mode_records_only_movement_with_preroll(db):
    mgr = RecordingManager(db)
    mgr.set_cameras([{"id": 2, "recording_mode": "motion"}])
    _feed(mgr, 2, 2, moving=False)        # still scene: nothing
    from app.models import Recording
    s = db.get_session()
    assert s.query(Recording).count() == 0
    s.close()
    _feed(mgr, 2, 1.5, moving=True)       # movement starts
    _feed(mgr, 2, 3, moving=False)        # stops; post-roll 1 s then close
    rows = _wait_closed(db, 1)
    assert rows[0].kind == "motion"
    d = _duration(rows[0].path)
    assert 2.0 <= d <= 4.5, d              # pre-roll + motion + post-roll
    mgr.stop_all()


def test_manual_recording_overrides_off(db):
    states = []
    mgr = RecordingManager(db, on_state=lambda cid, on: states.append(on))
    mgr.set_cameras([{"id": 3, "recording_mode": "off"}])
    _feed(mgr, 3, 0.5)
    mgr.set_manual(3, True)
    _feed(mgr, 3, 1.5)
    assert mgr.is_recording(3)
    mgr.set_manual(3, False)
    _feed(mgr, 3, 0.5)
    rows = _wait_closed(db, 1)
    assert rows[0].kind == "manual" and states[:2] == [True, False]
    mgr.stop_all()


def test_segments_roll_over(db, monkeypatch):
    monkeypatch.setattr(Config, "RECORDING_SEGMENT_MINUTES", 1 / 60)  # 1 s segments
    mgr = RecordingManager(db)
    mgr.set_cameras([{"id": 4, "recording_mode": "continuous"}])
    _feed(mgr, 4, 3.5)
    mgr.stop_all()
    assert len(_wait_closed(db, 3)) >= 3


def test_retention_by_age_and_size(db, monkeypatch):
    from app.models import Recording
    folder = os.path.join(Config.RECORDINGS_DIR, "cam9", "2020-01-01")
    os.makedirs(folder)
    s = db.get_session()
    for i, age_days in enumerate((60, 2, 1)):
        p = os.path.join(folder, f"f{i}.mp4")
        with open(p, "wb") as f:
            f.write(b"x" * 1_000_000)
        start = datetime.now() - timedelta(days=age_days)
        s.add(Recording(camera_id=9, path=p, kind="continuous", start_time=start,
                        end_time=start + timedelta(minutes=10), size_bytes=1_000_000))
    s.commit()
    s.close()
    monkeypatch.setattr(Config, "RECORDING_RETENTION_DAYS", 30)
    monkeypatch.setattr(Config, "RECORDING_MAX_GB", 1.5 / 1024)   # ~1.5 MB
    monkeypatch.setattr(Config, "RECORDING_MIN_FREE_GB", 0)
    mgr = RecordingManager(db)
    assert mgr.enforce_retention() == 2          # 60-day-old, then oldest for size
    s = db.get_session()
    left = s.query(Recording).all()
    s.close()
    assert len(left) == 1 and os.path.exists(left[0].path)


def test_find_recording_for_alert_time(db):
    from app.models import Recording
    t0 = datetime(2026, 5, 1, 10, 0, 0)
    s = db.get_session()
    s.add(Recording(camera_id=5, path="/x.mp4", kind="motion", start_time=t0,
                    end_time=t0 + timedelta(minutes=2)))
    s.commit()
    s.close()
    hit = find_recording_at(db, 5, t0 + timedelta(seconds=30))
    assert hit and hit["offset"] == 30
    assert find_recording_at(db, 5, t0 + timedelta(minutes=5)) is None


def test_motion_detector_levels():
    det = rec_mod.MotionDetector()
    a = np.zeros((240, 320, 3), np.uint8)
    b = a.copy()
    cv2.rectangle(b, (100, 100), (160, 160), (255, 255, 255), -1)
    det.update(a)
    assert det.update(a) == 0.0
    assert det.update(b) > rec_mod.MOTION_LEVELS["medium"]


def test_queued_frames_are_written_when_stopping(db, monkeypatch):
    """A slow start (e.g. codec loading on Windows) must not lose the tail."""
    import cv2 as _cv2
    real = _cv2.VideoWriter

    def slow_writer(*a, **k):
        time.sleep(1.5)
        return real(*a, **k)

    monkeypatch.setattr(rec_mod.cv2, "VideoWriter", slow_writer)
    mgr = RecordingManager(db)
    mgr.set_cameras([{"id": 8, "recording_mode": "continuous"}])
    _feed(mgr, 8, 1.2)          # all frames queue up while the file is opening
    mgr.stop_all(wait_s=10)
    rows = _wait_closed(db, 1)
    assert _duration(rows[0].path) >= 1.0
