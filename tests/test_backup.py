import os
import zipfile
from datetime import timedelta

import pytest

from app.config import Config
from app.core import backup, security


@pytest.fixture(autouse=True)
def key_cache():
    security.reset_key_cache()
    yield
    security.reset_key_cache()


def _seed(db):
    from app.core.camera_manager import CameraManager
    from app.models import Person
    CameraManager(db).add_camera({"name": "Gate", "brand": "Hikvision", "host": "1.2.3.4",
                                  "username": "admin", "password": "CamPass1"})
    s = db.get_session()
    s.add(Person(name="Asha"))
    s.commit()
    s.close()
    os.makedirs(os.path.join(Config.FACES_DIR, "person_1"), exist_ok=True)
    with open(os.path.join(Config.FACES_DIR, "person_1", "a.jpg"), "wb") as f:
        f.write(b"jpeg")
    Config.SHIFT_START = "07:45"
    Config.save_settings()


def test_backup_restore_roundtrip(db, tmp_path):
    from app.core.camera_manager import CameraManager
    from app.database import Database
    _seed(db)
    zpath = str(tmp_path / "b.zip")
    backup.create_backup(zpath)
    names = set(zipfile.ZipFile(zpath).namelist())
    assert {"manifest.json", "classguard.db", "settings.json", "secret.key",
            "faces/person_1/a.jpg"} <= names
    assert backup.inspect_backup(zpath)["has_key"]

    # Simulate a fresh PC: wipe data, then restore.
    db.close()
    for name in ("test.db", "test.db-wal", "test.db-shm", "settings.json", "secret.key"):
        p = os.path.join(Config.DATA_DIR, name)
        if os.path.exists(p):
            os.remove(p)
    import shutil
    shutil.rmtree(Config.FACES_DIR)
    security.reset_key_cache()
    backup.stage_restore(zpath)
    assert backup.restore_pending()
    # apply_pending_restore restores to the standard file name
    kept = backup.apply_pending_restore()
    assert kept and not backup.restore_pending()
    os.replace(os.path.join(Config.DATA_DIR, "classguard.db"), Config.DB_PATH)
    security.reset_key_cache()
    restored = Database(f"sqlite:///{Config.DB_PATH}")
    restored.init()
    cams = CameraManager(restored).get_all_cameras()
    assert cams[0]["password"] == "CamPass1"            # key came back too
    assert os.path.exists(os.path.join(Config.FACES_DIR, "person_1", "a.jpg"))
    Config.SHIFT_START = "09:00"
    Config.load_settings()
    assert Config.SHIFT_START == "07:45"
    restored.close()


@pytest.mark.parametrize("contents,msg", [
    ({"readme.txt": b"x"}, "Not a ClassGuard backup"),
    ({"manifest.json": b'{"format": 99}', "classguard.db": b""}, "newer version"),
    ({"manifest.json": b'{"format": 1}', "classguard.db": b"", "../evil": b"x"}, "unsafe"),
])
def test_bad_backups_are_rejected(tmp_path, contents, msg):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as f:
        for name, data in contents.items():
            f.writestr(name, data)
    with pytest.raises(ValueError, match=msg):
        backup.inspect_backup(str(z))


def test_auto_backup_keeps_last_seven(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(Config, "DB_PATH", Config.DB_PATH)
    made = [backup.auto_backup_if_due(every=timedelta(0)) for _ in range(9)]
    assert all(made)
    assert backup.auto_backup_if_due() is None             # not due again today
    assert len(os.listdir(backup.backups_dir())) == 7
