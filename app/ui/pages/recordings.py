import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton,
    QMessageBox
)
from PyQt6.QtCore import Qt
from app.config import Config


class RecordingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("Recordings")
        title.setObjectName("page_title")
        sub = QLabel(f"Stored at: {Config.RECORDINGS_DIR}")
        sub.setObjectName("page_subtitle")
        hdr.addWidget(title); hdr.addStretch()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.setObjectName("secondary_btn")
        btn_refresh.clicked.connect(self.refresh)
        btn_open_folder = QPushButton("Open Folder")
        btn_open_folder.setObjectName("secondary_btn")
        btn_open_folder.clicked.connect(self._open_folder)
        hdr.addWidget(btn_refresh)
        hdr.addWidget(btn_open_folder)
        root.addLayout(hdr)
        root.addWidget(sub)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "background:#1b1b30; color:#dde1ec;"
            "border:1px solid #252550; border-radius:4px;"
        )
        root.addWidget(self._list)

        btn_row = QHBoxLayout()
        btn_del = QPushButton("Delete Selected")
        btn_del.setObjectName("danger_btn")
        btn_del.clicked.connect(self._delete_selected)
        btn_row.addStretch(); btn_row.addWidget(btn_del)
        root.addLayout(btn_row)

    def refresh(self):
        self._list.clear()
        folder = Config.RECORDINGS_DIR
        if not os.path.isdir(folder):
            return
        files = sorted(
            [f for f in os.listdir(folder) if f.endswith((".mp4", ".avi"))],
            reverse=True
        )
        for f in files:
            path = os.path.join(folder, f)
            size_mb = os.path.getsize(path) / 1_048_576
            item = QListWidgetItem(f"  {f}  ({size_mb:.1f} MB)")
            item.setData(Qt.ItemDataRole.UserRole, path)
            self._list.addItem(item)

    def _delete_selected(self):
        items = self._list.selectedItems()
        if not items: return
        if QMessageBox.question(
            self, "Confirm", f"Delete {len(items)} file(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            for item in items:
                path = item.data(Qt.ItemDataRole.UserRole)
                try: os.remove(path)
                except Exception: pass
            self.refresh()

    def _open_folder(self):
        import subprocess, sys
        folder = Config.RECORDINGS_DIR
        if sys.platform == "win32":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.run(["open", folder])
        else:
            subprocess.run(["xdg-open", folder])
