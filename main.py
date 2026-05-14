import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from app.ui.main_window import MainWindow
from app.database import Database
from app.config import Config
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

def main():
    for d in [Config.DATA_DIR, Config.FACES_DIR, Config.RECORDINGS_DIR,
              Config.SNAPSHOTS_DIR, Config.LOGS_DIR]:
        os.makedirs(d, exist_ok=True)

    db = Database()
    db.init()

    app = QApplication(sys.argv)
    app.setApplicationName(Config.APP_NAME)
    app.setApplicationVersion(Config.VERSION)
    app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)

    window = MainWindow(db)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
