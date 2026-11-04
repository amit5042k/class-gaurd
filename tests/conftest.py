import os
import sys
import tempfile

# Must run before any app import: Config resolves ~/.classguard at import time.
_HOME = tempfile.mkdtemp(prefix="classguard-test-home-")
os.environ["HOME"] = _HOME
os.environ["USERPROFILE"] = _HOME
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from app.config import Config  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Each test gets its own data folders and default settings."""
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(Config, "SNAPSHOTS_DIR", str(tmp_path / "snapshots"))
    monkeypatch.setattr(Config, "FACES_DIR", str(tmp_path / "faces"))
    monkeypatch.setattr(Config, "LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(Config, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(Config, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(Config, "RECORDINGS_DIR", str(tmp_path / "recordings"))
    for key in ("SHIFT_START", "LATE_GRACE_MINUTES", "WEEKLY_OFF_DAYS",
                "ALERT_COOLDOWN", "SAFETY_MODEL", "ALERT_UNKNOWN_FACES",
                "AUTO_START_STREAMS", "FACE_RECOGNITION_THRESHOLD",
                "RECORDING_SEGMENT_MINUTES", "RECORDING_RETENTION_DAYS", "RECORDING_MAX_GB",
                "RECORDING_MIN_FREE_GB", "RECORDING_MAX_WIDTH", "MOTION_SENSITIVITY",
                "MOTION_PRE_ROLL", "MOTION_POST_ROLL"):
        monkeypatch.setattr(Config, key, getattr(Config, key))
    yield


@pytest.fixture
def db(tmp_path):
    from app.database import Database
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    database.init()
    yield database
    database.close()


def _have_face_recognition():
    try:
        import face_recognition  # noqa: F401
        import skimage.data  # noqa: F401
        return True
    except ImportError:
        return False


needs_face = pytest.mark.skipif(not _have_face_recognition(),
                                reason="face_recognition / scikit-image not installed")


@pytest.fixture
def face_image():
    """A single, clearly visible face (BGR), from scikit-image's sample data."""
    import cv2
    import skimage.data
    rgb = skimage.data.astronaut()[:260]  # crop: one face only
    return cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2BGR)
