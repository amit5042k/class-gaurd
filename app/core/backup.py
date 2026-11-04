"""Backup and restore of everything except recordings.

A backup zip holds: classguard.db (consistent snapshot via SQLite's backup API),
settings.json, secret.key (needed to read stored camera passwords), faces/ and
manifest.json. Restores are staged and applied at the next start, so the live
database is never replaced underneath the running app.
"""
import glob
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta
from typing import Optional

from app.config import Config

logger = logging.getLogger(__name__)

FORMAT = 1
REQUIRED_TABLES = {"cameras", "persons", "attendance_records"}


def backups_dir() -> str:
    return os.path.join(Config.DATA_DIR, "backups")


def _pending_dir() -> str:
    return os.path.join(Config.DATA_DIR, "restore_pending")


def create_backup(dest_zip: str, include_faces: bool = True) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(dest_zip)), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="cg-backup-")
    try:
        snap = os.path.join(tmp, "classguard.db")
        src = sqlite3.connect(Config.DB_PATH)
        dst = sqlite3.connect(snap)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        manifest = {"format": FORMAT, "app_version": Config.VERSION,
                    "created": datetime.now().isoformat(timespec="seconds"),
                    "faces": include_faces}
        part = dest_zip + ".part"
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
            z.write(snap, "classguard.db")
            for name in ("settings.json", "secret.key"):
                path = os.path.join(Config.DATA_DIR, name)
                if os.path.exists(path):
                    z.write(path, name)
            if include_faces and os.path.isdir(Config.FACES_DIR):
                for root, _, files in os.walk(Config.FACES_DIR):
                    for f in files:
                        full = os.path.join(root, f)
                        z.write(full, os.path.join("faces", os.path.relpath(full, Config.FACES_DIR)))
        os.replace(part, dest_zip)
        logger.info("Backup written to %s", dest_zip)
        return dest_zip
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def inspect_backup(zip_path: str) -> dict:
    """Validate a backup; returns its manifest or raises ValueError."""
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ValueError(f"Not a ClassGuard backup: {e}")
    with z:
        names = set(z.namelist())
        if "manifest.json" not in names or "classguard.db" not in names:
            raise ValueError("Not a ClassGuard backup (manifest or database missing).")
        for n in names:
            if n.startswith("/") or ".." in n.replace("\\", "/").split("/"):
                raise ValueError("The backup contains unsafe file paths.")
        manifest = json.loads(z.read("manifest.json"))
        if manifest.get("format", 0) > FORMAT:
            raise ValueError("This backup was made by a newer version of ClassGuard.")
        tmp = tempfile.mkdtemp(prefix="cg-check-")
        try:
            db = z.extract("classguard.db", tmp)
            con = sqlite3.connect(db)
            try:
                if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("The database in this backup is damaged.")
                tables = {r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                con.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if not REQUIRED_TABLES <= tables:
            raise ValueError("The backup database is missing ClassGuard tables.")
        manifest["has_key"] = "secret.key" in names
        return manifest


def stage_restore(zip_path: str) -> dict:
    """Check the backup and unpack it; it is applied on the next start."""
    manifest = inspect_backup(zip_path)
    pending = _pending_dir()
    shutil.rmtree(pending, ignore_errors=True)
    os.makedirs(pending)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(pending)
    return manifest


def restore_pending() -> bool:
    return os.path.isdir(_pending_dir()) and os.path.exists(
        os.path.join(_pending_dir(), "classguard.db"))


def apply_pending_restore() -> Optional[str]:
    """Called at start-up before the database is opened. Returns the folder
    where the replaced data was kept, or None if nothing was pending."""
    if not restore_pending():
        return None
    pending = _pending_dir()
    keep = os.path.join(Config.DATA_DIR, f"before_restore_{datetime.now():%Y%m%d_%H%M%S}")
    os.makedirs(keep)
    for name in ("classguard.db", "classguard.db-wal", "classguard.db-shm",
                 "settings.json", "secret.key"):
        cur = os.path.join(Config.DATA_DIR, name)
        if os.path.exists(cur):
            shutil.move(cur, os.path.join(keep, name))
    if os.path.isdir(Config.FACES_DIR) and os.path.isdir(os.path.join(pending, "faces")):
        shutil.move(Config.FACES_DIR, os.path.join(keep, "faces"))
    for name in ("classguard.db", "settings.json", "secret.key"):
        src = os.path.join(pending, name)
        if os.path.exists(src):
            shutil.move(src, os.path.join(Config.DATA_DIR, name))
    if os.path.isdir(os.path.join(pending, "faces")):
        shutil.move(os.path.join(pending, "faces"), Config.FACES_DIR)
    shutil.rmtree(pending, ignore_errors=True)
    logger.info("Backup restored; previous data kept in %s", keep)
    return keep


def auto_backup_if_due(keep: int = 7, every: timedelta = timedelta(days=1)) -> Optional[str]:
    folder = backups_dir()
    os.makedirs(folder, exist_ok=True)
    existing = sorted(glob.glob(os.path.join(folder, "auto_*.zip")))
    if existing:
        last = datetime.fromtimestamp(os.path.getmtime(existing[-1]))
        if datetime.now() - last < every:
            return None
    if not os.path.exists(Config.DB_PATH):
        return None
    path = create_backup(os.path.join(folder, f"auto_{datetime.now():%Y%m%d_%H%M%S_%f}.zip"))
    for old in sorted(glob.glob(os.path.join(folder, "auto_*.zip")))[:-keep]:
        try:
            os.remove(old)
        except OSError:
            pass
    return path


def start_auto_backup_thread():
    def run():
        try:
            auto_backup_if_due()
        except Exception:
            logger.exception("Automatic backup failed")
    threading.Thread(target=run, daemon=True, name="auto-backup").start()
