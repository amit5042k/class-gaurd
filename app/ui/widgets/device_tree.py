from typing import Dict, List

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QLabel, QLineEdit, QMenu, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget
)

from app.ui.widgets.video_cell import camera_mime

ROLE_CAMERA = Qt.ItemDataRole.UserRole + 1
STATE_COLORS = {"live": "#27ae60", "connecting": "#f39c12", "error": "#ef4565",
                "idle": "#6b6f86"}


class _Tree(QTreeWidget):
    def mimeData(self, items):
        cid = items[0].data(0, ROLE_CAMERA) if items else None
        return camera_mime(int(cid)) if cid is not None else None

    def mimeTypes(self):
        from app.ui.widgets.video_cell import CAMERA_MIME
        return [CAMERA_MIME]


class DeviceTree(QWidget):
    """Device list on the left of Live View: recorders (or groups) and their
    channels. Double-click or drag a channel onto a window to view it;
    double-click a recorder to show all of its channels."""

    open_requested = pyqtSignal(int)          # camera_id
    open_many_requested = pyqtSignal(list)    # camera ids
    diagnose_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: Dict[int, QTreeWidgetItem] = {}
        self._states: Dict[int, str] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        title = QLabel("Devices")
        title.setStyleSheet("font-weight:bold; color:#dde1ec;")
        lay.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search cameras…")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.tree = _Tree()
        self.tree.setHeaderHidden(True)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.tree.itemDoubleClicked.connect(self._on_double)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)
        hint = QLabel("Drag a camera onto a window, or double-click it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#777; font-size:11px;")
        lay.addWidget(hint)
        self.setMinimumWidth(190)
        self.setMaximumWidth(320)

    def set_cameras(self, cameras: List[Dict], nvr_names: Dict[int, str]):
        expanded = {self.tree.topLevelItem(i).text(0).split("  (")[0]
                    for i in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(i).isExpanded()}
        first = self.tree.topLevelItemCount() == 0
        self.tree.clear()
        self._items.clear()
        parents: Dict[str, QTreeWidgetItem] = {}
        for cam in cameras:
            nvr = nvr_names.get(cam.get("nvr_id")) if cam.get("nvr_id") else None
            key = nvr or cam.get("group_name") or "Default"
            parent = parents.get(key)
            if parent is None:
                parent = QTreeWidgetItem(self.tree, [key])
                parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
                parents[key] = parent
            item = QTreeWidgetItem(parent, [cam["name"]])
            item.setData(0, ROLE_CAMERA, cam["id"])
            item.setToolTip(0, f"{cam.get('brand', '')} {cam.get('host', '')}".strip())
            self._items[cam["id"]] = item
            self._paint(cam["id"])
        for key, parent in parents.items():
            parent.setText(0, f"{key}  ({parent.childCount()})")
            parent.setExpanded(first or key in expanded)
        self._filter(self.search.text())

    def set_state(self, camera_id: int, state: str):
        self._states[camera_id] = state
        self._paint(camera_id)

    def state(self, camera_id: int) -> str:
        return self._states.get(camera_id, "idle")

    def _paint(self, camera_id: int):
        item = self._items.get(camera_id)
        if item is not None:
            item.setForeground(0, QColor(STATE_COLORS.get(self.state(camera_id), "#6b6f86")))
            item.setText(0, ("● " if self.state(camera_id) == "live" else "○ ")
                         + item.text(0).lstrip("●○ "))

    def _filter(self, text: str):
        text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            shown = 0
            for j in range(parent.childCount()):
                child = parent.child(j)
                hide = bool(text) and text not in child.text(0).lower()
                child.setHidden(hide)
                shown += not hide
            parent.setHidden(shown == 0)
            if text:
                parent.setExpanded(True)

    def _children(self, parent: QTreeWidgetItem) -> List[int]:
        return [int(parent.child(j).data(0, ROLE_CAMERA)) for j in range(parent.childCount())
                if not parent.child(j).isHidden()]

    def _on_double(self, item: QTreeWidgetItem, _col: int):
        cid = item.data(0, ROLE_CAMERA)
        if cid is not None:
            self.open_requested.emit(int(cid))
        else:
            self.open_many_requested.emit(self._children(item))

    def _menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        cid = item.data(0, ROLE_CAMERA)
        if cid is not None:
            menu.addAction("Live view").triggered.connect(
                lambda: self.open_requested.emit(int(cid)))
            menu.addAction("Diagnose connection…").triggered.connect(
                lambda: self.diagnose_requested.emit(int(cid)))
        else:
            menu.addAction("Live view of all channels").triggered.connect(
                lambda: self.open_many_requested.emit(self._children(item)))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
