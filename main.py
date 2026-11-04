import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from PyQt6.QtWidgets import QApplication, QMessageBox

from app.config import Config


def setup_logging():
    os.makedirs(Config.LOGS_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file_handler = RotatingFileHandler(os.path.join(Config.LOGS_DIR, "classguard.log"),
                                       maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(file_handler)

    def excepthook(exc_type, exc, tb):
        logging.critical("Unhandled error", exc_info=(exc_type, exc, tb))
        if QApplication.instance():
            QMessageBox.critical(None, "Unexpected error",
                                 f"{exc_type.__name__}: {exc}\n\nDetails were written to "
                                 f"{os.path.join(Config.LOGS_DIR, 'classguard.log')}")
    sys.excepthook = excepthook


def main():
    if "--data-dir" in sys.argv:
        i = sys.argv.index("--data-dir")
        Config.set_data_dir(sys.argv[i + 1])
    if "--self-test" in sys.argv:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication(sys.argv)
        from app.selftest import run
        return run(app)

    for d in [Config.DATA_DIR, Config.FACES_DIR, Config.RECORDINGS_DIR,
              Config.SNAPSHOTS_DIR, Config.LOGS_DIR]:
        os.makedirs(d, exist_ok=True)
    setup_logging()
    from app.core.backup import apply_pending_restore, start_auto_backup_thread
    restored_from = apply_pending_restore()
    Config.load_settings()

    app = QApplication(sys.argv)
    app.setApplicationName(Config.APP_NAME)
    app.setApplicationVersion(Config.VERSION)
    from PyQt6.QtGui import QIcon
    from app.config import resource_path
    app.setWindowIcon(QIcon(resource_path("app", "assets", "icon.png")))

    from app.core.camera_manager import CameraManager
    from app.database import Database
    from app.ui.login import sign_in
    from app.ui.main_window import MainWindow
    db = Database()
    db.init()
    CameraManager(db).migrate_secrets()
    if restored_from:
        QMessageBox.information(None, "Backup restored",
                                "The backup was restored. Your previous data was kept in:\n"
                                f"{restored_from}")
    if Config.AUTO_BACKUP:
        start_auto_backup_thread()

    user = sign_in(db)
    if user is None:
        return 0
    window = MainWindow(db, user)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
