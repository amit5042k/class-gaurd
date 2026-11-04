import sqlite3

import pytest
from PyQt6.QtWidgets import QMessageBox

from app.config import Config
from app.core import security
from app.core.security import (
    ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, CurrentUser, UserService, decrypt_secret,
    encrypt_secret, hash_password, verify_password
)


@pytest.fixture(autouse=True)
def fresh_key():
    security.reset_key_cache()
    yield
    security.reset_key_cache()


def test_password_hashing():
    h = hash_password("Secret123")
    assert h.startswith("pbkdf2_sha256$") and "Secret123" not in h
    assert verify_password("Secret123", h) and not verify_password("secret123", h)
    assert hash_password("Secret123") != h          # salted


def test_secret_encryption_roundtrip_and_key_file():
    import os
    token = encrypt_secret("cam-pass@1")
    assert token.startswith("enc1:") and "cam-pass" not in token
    assert decrypt_secret(token) == "cam-pass@1"
    assert decrypt_secret("legacy-plain") == "legacy-plain"
    assert os.path.exists(security.key_path())
    if os.name == "posix":
        assert oct(os.stat(security.key_path()).st_mode & 0o777) == "0o600"


def test_camera_passwords_are_stored_encrypted(db, tmp_path):
    from app.core.camera_manager import CameraManager
    mgr = CameraManager(db)
    cam = mgr.add_camera({"name": "Gate", "brand": "Hikvision", "host": "10.0.0.5",
                          "username": "admin", "password": "TopSecret9"})
    raw = sqlite3.connect(tmp_path / "test.db").execute(
        "SELECT password, rtsp_url FROM cameras").fetchone()
    assert raw[0].startswith("enc1:") and raw[1] is None
    d = mgr.get_camera(cam.id)
    assert d["password"] == "TopSecret9"
    assert d["rtsp_url"] == "rtsp://admin:TopSecret9@10.0.0.5:554/Streaming/Channels/101"
    mgr.update_camera(cam.id, {"rtsp_url": "rtsp://admin:TopSecret9@10.0.0.5/custom"})
    raw = sqlite3.connect(tmp_path / "test.db").execute("SELECT rtsp_url FROM cameras").fetchone()
    assert raw[0].startswith("enc1:")
    assert mgr.get_camera(cam.id)["custom_rtsp_url"].endswith("/custom")


def test_old_plaintext_database_is_migrated(db, tmp_path):
    from app.core.camera_manager import CameraManager
    con = sqlite3.connect(tmp_path / "test.db")
    con.execute("INSERT INTO cameras (name, brand, host, port, username, password, channel, "
                "rtsp_url, use_substream, is_active) VALUES ('A','Dahua','1.1.1.1',554,'admin',"
                "'pw1','1', 'rtsp://admin:pw1@1.1.1.1:554/cam/realmonitor?channel=1&subtype=0',0,1)")
    con.execute("INSERT INTO cameras (name, brand, host, port, username, password, channel, "
                "rtsp_url, use_substream, is_active) VALUES ('B','Generic RTSP','2.2.2.2',554,"
                "'u','pw2',1,'rtsp://u:pw2@2.2.2.2/live',0,1)")
    con.execute("INSERT INTO nvrs (name, brand, host, password) VALUES ('N','Dahua','3.3.3.3','pw3')")
    con.commit()
    mgr = CameraManager(db)
    assert mgr.migrate_secrets() == 3
    assert mgr.migrate_secrets() == 0                 # idempotent
    rows = con.execute("SELECT password, rtsp_url FROM cameras ORDER BY name").fetchall()
    assert rows[0][1] is None                         # automatic URL no longer stored
    assert rows[1][1].startswith("enc1:")             # custom URL kept, encrypted
    assert all(r[0].startswith("enc1:") for r in rows)
    assert con.execute("SELECT password FROM nvrs").fetchone()[0].startswith("enc1:")
    cams = {c["name"]: c for c in mgr.get_all_cameras()}
    assert cams["A"]["rtsp_url"].startswith("rtsp://admin:pw1@1.1.1.1")
    assert cams["B"]["rtsp_url"] == "rtsp://u:pw2@2.2.2.2/live"
    assert mgr.get_all_nvrs()[0]["password"] == "pw3"


def test_users_lockout_and_last_admin(db):
    users = UserService(db)
    assert not users.has_users()
    with pytest.raises(ValueError):
        users.create("x", "short", ROLE_ADMIN)
    admin = users.create("Admin", "Admin12345", ROLE_ADMIN)
    viewer = users.create("guard", "Guard12345", ROLE_VIEWER)
    assert users.authenticate("ADMIN", "Admin12345").role == ROLE_ADMIN
    for _ in range(users.MAX_ATTEMPTS):
        assert users.authenticate("guard", "wrong") is None
    assert users.locked_for("guard") > 0
    assert users.authenticate("guard", "Guard12345") is None      # locked even if right
    with pytest.raises(ValueError):
        users.update(admin, role=ROLE_OPERATOR)                   # last admin
    with pytest.raises(ValueError):
        users.delete(admin)
    users.update(viewer, role=ROLE_ADMIN)
    users.update(admin, role=ROLE_OPERATOR)                       # fine now
    from app.models import AuditLog
    s = db.get_session()
    actions = [a.action for a in s.query(AuditLog)]
    s.close()
    assert "login" in actions and actions.count("login_failed") == users.MAX_ATTEMPTS
    assert "login_blocked" in actions


def test_role_pages():
    viewer = CurrentUser(1, "v", ROLE_VIEWER)
    op = CurrentUser(2, "o", ROLE_OPERATOR)
    assert viewer.can("live_view") and not viewer.can("cameras") and not viewer.can_operate
    assert op.can("face_enrollment") and not op.can("settings") and op.can_operate


def test_login_dialog(qtbot, db):
    from app.ui.login import LoginDialog
    users = UserService(db)
    users.create("op", "Operator1", ROLE_OPERATOR)
    dlg = LoginDialog(users)
    qtbot.addWidget(dlg)
    dlg.username.setText("op")
    dlg.password.setText("nope")
    dlg._ok()
    assert dlg.user is None and "Wrong" in dlg.error.text()
    dlg.password.setText("Operator1")
    dlg._ok()
    assert dlg.user and dlg.user.role == ROLE_OPERATOR


def test_viewer_sees_limited_app(qtbot, db, monkeypatch):
    from app.core.alerts import AlertService
    from app.ui.main_window import MainWindow
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    AlertService(db).raise_alert(None, "Test", "x", "high")
    w = MainWindow(db, CurrentUser(5, "guard", ROLE_VIEWER, "Gate Guard"))
    qtbot.addWidget(w)
    try:
        assert w._sidebar.is_allowed("live_view") and not w._sidebar.is_allowed("cameras")
        assert not w._sidebar.is_allowed("users") and not w._sidebar.is_allowed("settings")
        w._sidebar.set_active("cameras")                 # ignored for viewers
        assert w._stack.currentWidget() is not w._p_cameras
        assert not w._p_alerts._btn_ack_all.isEnabled()
        assert not w._p_rec._btn_delete.isEnabled()
        assert not w._p_live._btn_stop_all.isEnabled()
        assert "Gate Guard" in w._sidebar._user_lbl.text()
        w.apply_user(CurrentUser(6, "boss", ROLE_ADMIN))
        assert w._sidebar.is_allowed("users") and w._p_alerts._btn_ack_all.isEnabled()
    finally:
        w.close()
