from typing import Dict, List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QWidget

from app.ui.widgets.video_cell import VideoCell


class CameraGrid(QWidget):
    """Grid of video tiles. Frames are routed by camera id, so the grid can be
    rebuilt (layout, page, single view) without touching the streams."""

    cell_double_clicked = pyqtSignal(int)
    cell_clicked = pyqtSignal(int)
    snapshot_requested = pyqtSignal(int)
    record_toggled = pyqtSignal(int, bool)
    fullscreen_requested = pyqtSignal(int)
    diagnose_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cells: Dict[int, VideoCell] = {}
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(4)
        self._rows = 2
        self._cols = 2
        self._selected: Optional[int] = None

    def set_layout(self, rows: int, cols: int):
        self._rows = rows
        self._cols = cols

    @property
    def page_size(self) -> int:
        return self._rows * self._cols

    def set_cameras(self, cameras: List[Dict], rows: int = None, cols: int = None):
        rows, cols = rows or self._rows, cols or self._cols
        for i in reversed(range(self._layout.count())):
            w = self._layout.itemAt(i).widget()
            if w:
                self._layout.removeWidget(w)
                w.deleteLater()
        self._cells.clear()
        for r in range(self._layout.rowCount()):
            self._layout.setRowStretch(r, 0)
        for c in range(self._layout.columnCount()):
            self._layout.setColumnStretch(c, 0)

        total = rows * cols
        for i, cam in enumerate(cameras[:total]):
            cell = VideoCell(cam["id"], cam["name"])
            cell.double_clicked.connect(self.cell_double_clicked)
            cell.clicked.connect(self._on_click)
            cell.snapshot_requested.connect(self.snapshot_requested)
            cell.record_toggled.connect(self.record_toggled)
            cell.fullscreen_requested.connect(self.fullscreen_requested)
            cell.diagnose_requested.connect(self.diagnose_requested)
            cell.set_selected(cam["id"] == self._selected)
            self._cells[cam["id"]] = cell
            r, c = divmod(i, cols)
            self._layout.addWidget(cell, r, c)
        for j in range(min(len(cameras), total), total):
            r, c = divmod(j, cols)
            self._layout.addWidget(VideoCell(-1 - j, ""), r, c)
        for r in range(rows):
            self._layout.setRowStretch(r, 1)
        for c in range(cols):
            self._layout.setColumnStretch(c, 1)

    def _on_click(self, camera_id: int):
        self.select(camera_id)
        self.cell_clicked.emit(camera_id)

    def select(self, camera_id: Optional[int]):
        self._selected = camera_id
        for cid, cell in self._cells.items():
            cell.set_selected(cid == camera_id)

    def update_frame(self, camera_id: int, img):
        cell = self._cells.get(camera_id)
        if cell:
            cell.update_frame(camera_id, img)

    def update_status(self, camera_id: int, connected: bool, msg: str = ""):
        cell = self._cells.get(camera_id)
        if cell:
            cell.set_status(connected, msg)

    def set_recording(self, camera_id: int, recording: bool, manual: bool = False):
        cell = self._cells.get(camera_id)
        if cell:
            cell.set_recording(recording, manual)

    def get_cell(self, camera_id: int) -> Optional[VideoCell]:
        return self._cells.get(camera_id)

    def visible_ids(self) -> List[int]:
        return list(self._cells)
