from typing import Dict, List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QWidget

from app.ui.widgets.video_cell import VideoCell

# Window counts offered in Live View. 6 and 8 have one large window.
WINDOW_COUNTS = [1, 4, 6, 8, 9, 16, 25, 36]


def window_positions(count: int):
    """(rows, cols, [(row, col, row_span, col_span), ...]) for a window count."""
    if count == 6:
        return 3, 3, [(0, 0, 2, 2), (0, 2, 1, 1), (1, 2, 1, 1),
                      (2, 0, 1, 1), (2, 1, 1, 1), (2, 2, 1, 1)]
    if count == 8:
        return 4, 4, [(0, 0, 3, 3)] + [(r, 3, 1, 1) for r in range(3)] + \
            [(3, c, 1, 1) for c in range(4)]
    side = 1
    while side * side < count:
        side += 1
    rows = side if side * (side - 1) < count else side - 1
    return rows, side, [(i // side, i % side, 1, 1) for i in range(count)]


class CameraGrid(QWidget):
    """Grid of playback windows. Frames are routed by camera id, so the grid can
    be rebuilt (layout, page, single view) without touching the streams."""

    cell_double_clicked = pyqtSignal(int)
    cell_clicked = pyqtSignal(int)
    snapshot_requested = pyqtSignal(int)
    record_toggled = pyqtSignal(int, bool)
    fullscreen_requested = pyqtSignal(int)
    diagnose_requested = pyqtSignal(int)
    close_requested = pyqtSignal(int)
    playback_requested = pyqtSignal(int)
    camera_dropped = pyqtSignal(int, int, int)   # window, camera_id, from window
    window_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cells: Dict[int, VideoCell] = {}
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(4)
        self._count = 4
        self._selected: Optional[int] = None
        self._operator = True

    def set_windows(self, count: int):
        self._count = max(1, count)

    def set_layout(self, rows: int, cols: int):
        self.set_windows(rows * cols)

    @property
    def page_size(self) -> int:
        return self._count

    def set_operator(self, can_operate: bool):
        self._operator = can_operate
        for cell in self._cells.values():
            cell.set_operator(can_operate)

    def set_cameras(self, cameras: List[Optional[Dict]], rows: int = None, cols: int = None):
        """One entry per window; None = empty window. rows/cols force a plain grid."""
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

        if rows and cols:
            total = rows * cols
            positions = [(i // cols, i % cols, 1, 1) for i in range(total)]
        else:
            rows, cols, positions = window_positions(self._count)
        for slot, (r, c, rs, cs) in enumerate(positions):
            cam = cameras[slot] if slot < len(cameras) else None
            if cam:
                cell = VideoCell(cam["id"], cam["name"], slot=slot)
                cell.double_clicked.connect(self.cell_double_clicked)
                cell.clicked.connect(self._on_click)
                cell.snapshot_requested.connect(self.snapshot_requested)
                cell.record_toggled.connect(self.record_toggled)
                cell.fullscreen_requested.connect(self.fullscreen_requested)
                cell.diagnose_requested.connect(self.diagnose_requested)
                cell.close_requested.connect(self.close_requested)
                cell.playback_requested.connect(self.playback_requested)
                cell.set_selected(cam["id"] == self._selected)
                cell.set_operator(self._operator)
                self._cells[cam["id"]] = cell
            else:
                cell = VideoCell(-1 - slot, "", slot=slot)
            cell.camera_dropped.connect(self.camera_dropped)
            cell.window_clicked.connect(self.window_clicked)
            self._layout.addWidget(cell, r, c, rs, cs)
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
