from PyQt6.QtWidgets import QWidget, QGridLayout, QSizePolicy
from PyQt6.QtCore import pyqtSignal
from app.ui.widgets.video_cell import VideoCell
from typing import List, Dict


class CameraGrid(QWidget):
    cell_double_clicked = pyqtSignal(int)
    snapshot_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cells: Dict[int, VideoCell] = {}
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(4)
        self._rows = 2
        self._cols = 2

    def set_layout(self, rows: int, cols: int):
        self._rows = rows
        self._cols = cols
        self._rebuild_grid()

    def set_cameras(self, cameras: List[Dict]):
        # clear old
        for i in reversed(range(self._layout.count())):
            w = self._layout.itemAt(i).widget()
            if w:
                self._layout.removeWidget(w)
                w.setParent(None)
        self._cells.clear()

        for i, cam in enumerate(cameras[: self._rows * self._cols]):
            cell = VideoCell(cam["id"], cam["name"])
            cell.double_clicked.connect(self.cell_double_clicked)
            cell.snapshot_requested.connect(self.snapshot_requested)
            self._cells[cam["id"]] = cell
            r, c = divmod(i, self._cols)
            self._layout.addWidget(cell, r, c)

        # fill remaining slots with empty placeholders
        total = self._rows * self._cols
        for j in range(len(cameras), total):
            ph = VideoCell(-j, "")
            r, c = divmod(j, self._cols)
            self._layout.addWidget(ph, r, c)

    def update_frame(self, camera_id: int, img):
        cell = self._cells.get(camera_id)
        if cell:
            cell.update_frame(camera_id, img)

    def update_status(self, camera_id: int, connected: bool, msg: str):
        cell = self._cells.get(camera_id)
        if cell:
            cell.set_status(connected, msg)

    def set_recording(self, camera_id: int, recording: bool):
        cell = self._cells.get(camera_id)
        if cell:
            cell.set_recording(recording)

    def _rebuild_grid(self):
        # force re-render on layout change
        self.update()

    def get_cell(self, camera_id: int) -> VideoCell:
        return self._cells.get(camera_id)
