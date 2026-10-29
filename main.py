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
    for d in [Config.DATA_DIR, Config.FACES_DIR, Config.RECORDINGS_DIR,
              Config.SNAPSHOTS_DIR, Config.LOGS_DIR]:
        os.makedirs(d, exist_ok=True)
    setup_logging()
    Config.load_settings()

    app = QApplication(sys.argv)
    app.setApplicationName(Config.APP_NAME)
    app.setApplicationVersion(Config.VERSION)

    from app.database import Database
    from app.ui.main_window import MainWindow
    db = Database()
    db.init()

    window = MainWindow(db)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
