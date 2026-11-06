import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import cv2
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QSplitter, QToolButton, QVBoxLayout,
    QWidget
)

from app.config import Config
from app.core.stream_manager import StreamManager
from app.ui.async_task import run_async
from app.ui.widgets.camera_grid import WINDOW_COUNTS, CameraGrid
from app.ui.widgets.device_tree import DeviceTree
from app.ui.widgets.ptz_panel import PTZPanel

ALL_GROUPS = "All groups"
CONNECTING_TEXT = "Connecting…"


def keyframe_requester(cam: Dict):
    """Callable asking the device for a keyframe (Hikvision ISAPI), or None."""
    if cam.get("brand") != "Hikvision" or cam.get("custom_rtsp_url"):
        return None
    stream_id = int(cam.get("channel") or 1) * 100 + (2 if cam.get("use_substream") else 1)

    def request():
        from app.protocols.hikvision import HikvisionAPI
        HikvisionAPI(cam["host"], cam.get("http_port") or 80, cam.get("username", ""),
                     cam.get("password", "")).request_keyframe(stream_id)
    return request


def snapshot_fetcher(cam: Dict):
    """Callable returning a still picture (BGR array) from the device, or None.
    Used when the NVR's video arrives without full pictures (grey tiles)."""
    if cam.get("brand") != "Hikvision" or cam.get("custom_rtsp_url"):
        return None
    channel = int(cam.get("channel") or 1)
    ids = [channel * 100 + (2 if cam.get("use_substream") else 1), channel * 100 + 1]
    state = {"api": None}

    def fetch():
        import cv2
        import numpy as np
        from app.protocols.hikvision import HikvisionAPI
        if state["api"] is None:
            state["api"] = HikvisionAPI(cam["host"], cam.get("http_port") or 80,
                                        cam.get("username", ""), cam.get("password", ""))
        for sid in dict.fromkeys(ids):
            data = state["api"].snapshot(sid)
            if data:
                img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    ids[:] = [sid]        # remember the stream that answers
                    return img
        return None
    return fetch


class LiveView(QWidget):
    """Main view in the style of desktop VMS clients (iVMS-4200, KVMS Pro):
    device tree on the left, playback windows in the middle, PTZ on the right,
    window toolbar and event list below."""

    active_count_changed = pyqtSignal(int)
    message = pyqtSignal(str)
    fullscreen_requested = pyqtSignal()
    record_requested = pyqtSignal(int, bool)   # camera_id, on
    playback_requested = pyqtSignal(int, object)   # camera_id, datetime

    def __init__(self, db, stream_manager: StreamManager, recorder=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.sm = stream_manager
        self.recorder = recorder
        self._cameras: List[Dict] = []
        self._by_id: Dict[int, Dict] = {}
        self._connected = set()
        self._status: Dict[int, tuple] = {}   # camera_id -> (ok, message), survives re-render
        self._start_queue: List[int] = []
        self._live = False             # Start All pressed: stream what's on screen
        self._pinned: set = set()      # needed elsewhere (recording, AI, monitoring)
        self._recording: Dict[int, bool] = {}
        self._page = 0
        self._slots: List[Optional[int]] = []   # camera per window
        self._manual = False           # windows filled by hand (drag / double-click)
        self._single: Optional[int] = None
        self._selected: Optional[int] = None
        self._selected_slot: Optional[int] = None
        self._tour = QTimer(self)
        self._tour.timeout.connect(self._tour_step)
        self._build_ui()

    # ------------------------------------------------------------------- UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self._toolbar = QWidget()
        toolbar = QHBoxLayout(self._toolbar)
        toolbar.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Live View")
        title.setObjectName("page_title")
        toolbar.addWidget(title)
        toolbar.addStretch()
        self._group_cb = QComboBox()
        self._group_cb.setMinimumWidth(140)
        self._group_cb.currentIndexChanged.connect(self._on_group_change)
        toolbar.addWidget(self._group_cb)
        self._btn_start_all = QPushButton("Start All")
        self._btn_start_all.setObjectName("success_btn")
        self._btn_start_all.clicked.connect(self.start_all_streams)
        self._btn_stop_all = QPushButton("Stop All")
        self._btn_stop_all.setObjectName("danger_btn")
        self._btn_stop_all.clicked.connect(self.stop_all_streams)
        toolbar.addWidget(self._btn_start_all)
        toolbar.addWidget(self._btn_stop_all)
        root.addWidget(self._toolbar)

        body = QSplitter(Qt.Orientation.Horizontal)
        self.tree = DeviceTree()
        self.tree.open_requested.connect(self.open_in_window)
        self.tree.open_many_requested.connect(self.open_many)
        self.tree.diagnose_requested.connect(self.diagnose)
        body.addWidget(self.tree)

        centre = QWidget()
        cl = QVBoxLayout(centre)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)
        self._grid = CameraGrid()
        self._grid.set_windows(4)
        self._grid.snapshot_requested.connect(self._take_snapshot)
        self._grid.cell_double_clicked.connect(self._toggle_single)
        self._grid.cell_clicked.connect(self._select)
        self._grid.record_toggled.connect(self.record_requested)
        self._grid.fullscreen_requested.connect(lambda _cid: self.fullscreen_requested.emit())
        self._grid.diagnose_requested.connect(self.diagnose)
        self._grid.close_requested.connect(self.close_window)
        self._grid.playback_requested.connect(self._instant_playback)
        self._grid.camera_dropped.connect(self._on_drop)
        self._grid.window_clicked.connect(self._on_window_clicked)
        cl.addWidget(self._grid, 1)
        self._bottom = self._build_bottom_bar()
        cl.addWidget(self._bottom)
        self._events = self._build_events()
        cl.addWidget(self._events)
        body.addWidget(centre)

        self.ptz = PTZPanel()
        self.ptz.record_toggled.connect(self.record_requested)
        self.ptz.snapshot_requested.connect(self._take_snapshot)
        body.addWidget(self.ptz)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setStretchFactor(2, 0)
        body.setSizes([220, 900, 220])
        root.addWidget(body, 1)

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(QLabel("Windows:"))
        self._layout_cb = QComboBox()
        for n in WINDOW_COUNTS:
            self._layout_cb.addItem(f"{n} window" + ("s" if n > 1 else ""), n)
        self._layout_cb.setCurrentIndex(WINDOW_COUNTS.index(4))
        self._layout_cb.currentIndexChanged.connect(self._on_layout_change)
        bl.addWidget(self._layout_cb)
        self._btn_prev = QPushButton("◀")
        self._btn_next = QPushButton("▶")
        self._page_lbl = QLabel()
        self._btn_prev.clicked.connect(lambda: self._go_page(-1))
        self._btn_next.clicked.connect(lambda: self._go_page(1))
        for w in (self._btn_prev, self._page_lbl, self._btn_next):
            bl.addWidget(w)

        self._btn_views = QToolButton()
        self._btn_views.setText("Views")
        self._btn_views.setToolTip("Save the current windows as a view, or open a saved one")
        self._btn_views.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._views_menu = QMenu(self._btn_views)
        self._views_menu.aboutToShow.connect(self._fill_views_menu)
        self._btn_views.setMenu(self._views_menu)
        bl.addWidget(self._btn_views)

        self._btn_tour = QPushButton("Auto-switch")
        self._btn_tour.setCheckable(True)
        self._btn_tour.setToolTip("Cycle through the pages of cameras automatically")
        self._btn_tour.toggled.connect(self._toggle_tour)
        self._tour_cb = QComboBox()
        for sec in (10, 20, 30, 60, 120):
            self._tour_cb.addItem(f"every {sec} s", sec)
        self._tour_cb.setCurrentIndex(1)
        self._tour_cb.currentIndexChanged.connect(
            lambda _i: self._tour.isActive() and self._tour.start(self._tour_cb.currentData() * 1000))
        bl.addWidget(self._btn_tour)
        bl.addWidget(self._tour_cb)
        bl.addStretch()
        self._btn_capture = QPushButton("Capture all")
        self._btn_capture.setToolTip("Save a snapshot of every window")
        self._btn_capture.clicked.connect(self.capture_all)
        self._btn_clear = QPushButton("Close all windows")
        self._btn_clear.clicked.connect(self.close_all_windows)
        self._btn_full = QPushButton("Full screen")
        self._btn_full.setObjectName("secondary_btn")
        self._btn_full.clicked.connect(self.fullscreen_requested)
        for w in (self._btn_capture, self._btn_clear, self._btn_full):
            bl.addWidget(w)
        return bar

    def _build_events(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        head = QHBoxLayout()
        lbl = QLabel("Events")
        lbl.setStyleSheet("font-weight:bold;")
        head.addWidget(lbl)
        hint = QLabel("double-click an event to play its video")
        hint.setStyleSheet("color:#777; font-size:11px;")
        head.addWidget(hint)
        head.addStretch()
        self._btn_events = QToolButton()
        self._btn_events.setText("Hide")
        self._btn_events.clicked.connect(self._toggle_events)
        head.addWidget(self._btn_events)
        lay.addLayout(head)
        self._event_list = QListWidget()
        self._event_list.setMaximumHeight(110)
        self._event_list.itemDoubleClicked.connect(self._on_event_double)
        lay.addWidget(self._event_list)
        return box

    def _toggle_events(self):
        show = not self._event_list.isVisible()
        self._event_list.setVisible(show)
        self._btn_events.setText("Hide" if show else "Show")

    def set_permissions(self, user):
        self.ptz.set_operator(user.can_operate)
        self._grid.set_operator(user.can_operate)
        self._btn_stop_all.setEnabled(user.can_operate)

    def set_chrome_visible(self, visible: bool):
        """Hide toolbars and side panels in full-screen mode."""
        for w in (self._toolbar, self.ptz, self.tree, self._bottom, self._events):
            w.setVisible(visible)

    # --------------------------------------------------------------- cameras
    def load_cameras(self, cameras: List[Dict]):
        old = {c["id"]: c.get("rtsp_url") for c in self._cameras}
        self._cameras = cameras
        self._by_id = {c["id"]: c for c in cameras}
        new_ids = set(self._by_id)
        for cid in list(old):
            if cid not in new_ids and self.sm.is_streaming(cid):
                self.sm.stop_stream(cid)
                self._connected.discard(cid)
        groups = sorted({c.get("group_name") or "Default" for c in cameras})
        current = self._group_cb.currentText()
        self._group_cb.blockSignals(True)
        self._group_cb.clear()
        self._group_cb.addItem(ALL_GROUPS)
        self._group_cb.addItems(groups)
        idx = self._group_cb.findText(current)
        self._group_cb.setCurrentIndex(max(0, idx))
        self._group_cb.blockSignals(False)
        self.tree.set_cameras(cameras, self._nvr_names())
        for cid in new_ids:
            self._tree_state(cid)
        if self._single not in new_ids:
            self._single = None
        self._slots = [s if s in new_ids else None for s in self._slots]
        if self._selected not in new_ids:
            self._select(None)
        else:
            self._select(self._selected)
        if not self._manual:
            self._fill_page()
        self._render()
        for cam in cameras:
            if self.sm.is_streaming(cam["id"]) and old.get(cam["id"]) != cam.get("rtsp_url"):
                self.start_camera(cam["id"])
        self._emit_count()

    def _nvr_names(self) -> Dict[int, str]:
        if self.db is None:
            return {}
        try:
            from app.core.camera_manager import CameraManager
            return {n["id"]: n["name"] for n in CameraManager(self.db).get_all_nvrs()}
        except Exception:
            return {}

    def _visible_cameras(self) -> List[Dict]:
        group = self._group_cb.currentText()
        if not group or group == ALL_GROUPS:
            return list(self._cameras)
        return [c for c in self._cameras if (c.get("group_name") or "Default") == group]

    def _pages(self) -> int:
        return max(1, -(-len(self._visible_cameras()) // self._grid.page_size))

    def _fill_page(self):
        """Windows show one page of the camera list (the default, like Start All)."""
        size = self._grid.page_size
        self._page = min(self._page, self._pages() - 1)
        ids = [c["id"] for c in self._visible_cameras()[self._page * size:(self._page + 1) * size]]
        self._slots = ids + [None] * (size - len(ids))

    def _fit_slots(self):
        size = self._grid.page_size
        self._slots = (self._slots + [None] * size)[:size]

    def _render(self):
        if self._single is not None:
            single = [self._by_id[self._single]] if self._single in self._by_id else []
            self._grid.set_cameras(single, 1, 1)
            self._page_lbl.setText("Single view")
            self._btn_prev.setEnabled(False)
            self._btn_next.setEnabled(False)
        else:
            self._fit_slots()
            self._grid.set_cameras([self._by_id.get(s) if s is not None else None
                                    for s in self._slots])
            pages = self._pages()
            self._page_lbl.setText("Custom view" if self._manual
                                   else f"Page {self._page + 1}/{pages}")
            self._btn_prev.setEnabled(self._manual or self._page > 0)
            self._btn_next.setEnabled(self._manual or self._page < pages - 1)
        for cid in self._grid.visible_ids():
            if cid in self._status and self.sm.is_streaming(cid):
                self._grid.update_status(cid, *self._status[cid])
            elif not self.sm.is_streaming(cid):
                self._grid.update_status(cid, False, "Waiting to connect…" if self._live
                                         else "Not started. Press Start All.")
            self._grid.set_recording(cid, self._recording.get(cid, False),
                                     bool(self.recorder and self.recorder.is_manual(cid)))
        self._grid.select(self._selected)
        self._sync_streams()

    def _sync_streams(self):
        """Like iVMS: only cameras on screen stream, so a 32-channel NVR isn't
        asked for 32 streams at once. Pinned cameras (recording / AI) keep going."""
        if not self._live:
            return
        visible = set(self._grid.visible_ids())
        for cam in self._cameras:
            cid = cam["id"]
            if cid not in visible and cid not in self._pinned and self.sm.is_streaming(cid):
                self.sm.stop_stream(cid)
                self._connected.discard(cid)
                self._status.pop(cid, None)
                self._tree_state(cid)
        self._start_queue = [cid for cid in self._start_queue
                             if cid in visible or cid in self._pinned]
        for cid in self._grid.visible_ids():
            if not self.sm.is_streaming(cid) and cid not in self._start_queue:
                self._start_queue.append(cid)
        self._start_next()
        self._emit_count()

    def set_pinned(self, ids):
        self._pinned = set(ids)

    def _on_layout_change(self, _idx):
        self._grid.set_windows(self._layout_cb.currentData())
        self._single = None
        if self._manual:
            self._fit_slots()        # keep the cameras you placed; new windows start empty
        else:
            self._page = 0
            self._fill_page()
        self._render()

    def _set_windows(self, count: int):
        self._layout_cb.blockSignals(True)
        self._layout_cb.setCurrentIndex(WINDOW_COUNTS.index(count))
        self._layout_cb.blockSignals(False)
        self._grid.set_windows(count)

    def _on_group_change(self, _idx):
        self._page = 0
        self._single = None
        self._manual = False
        self._fill_page()
        self._render()

    def _go_page(self, delta: int):
        if self._manual:          # leave the custom view for the camera pages
            self._manual = False
            self._page = 0 if delta > 0 else self._pages() - 1
        else:
            self._page = max(0, min(self._pages() - 1, self._page + delta))
        self._single = None
        self._fill_page()
        self._render()

    def _toggle_single(self, camera_id: int):
        self._single = None if self._single == camera_id else camera_id
        self._select(camera_id)
        self._render()

    def _select(self, camera_id: Optional[int]):
        self._selected = camera_id
        cam = self._by_id.get(camera_id)
        self.ptz.set_camera(cam, bool(self.recorder and camera_id is not None
                                      and self.recorder.is_manual(camera_id)))
        self._grid.select(camera_id)
        if camera_id in self._slots:
            self._selected_slot = self._slots.index(camera_id)

    def _on_window_clicked(self, slot: int):
        self._selected_slot = slot
        if slot < len(self._slots) and self._slots[slot] is None:
            self._select(None)

    # ------------------------------------------------------- window placement
    def _go_custom(self):
        if not self._manual:
            self._manual = True
            self._btn_tour.setChecked(False)
        self._single = None
        self._live = True

    def open_in_window(self, camera_id: int, slot: Optional[int] = None):
        """Show a camera in a window: the given one, else the selected empty
        window, else the first empty one, else the selected window."""
        if camera_id not in self._by_id:
            return
        self._go_custom()
        self._fit_slots()
        if camera_id in self._slots and slot is None:
            self._select(camera_id)
            self._render()
            return
        if slot is None:
            sel = self._selected_slot
            if sel is not None and sel < len(self._slots) and self._slots[sel] is None:
                slot = sel
            elif None in self._slots:
                slot = self._slots.index(None)
            else:
                slot = sel if sel is not None and sel < len(self._slots) else 0
        if camera_id in self._slots:
            self._slots[self._slots.index(camera_id)] = self._slots[slot]
        self._slots[slot] = camera_id
        self._selected_slot = slot
        self._select(camera_id)
        self._render()

    def open_many(self, camera_ids: List[int]):
        ids = [c for c in camera_ids if c in self._by_id]
        if not ids:
            return
        self._go_custom()
        count = next((n for n in WINDOW_COUNTS if n >= len(ids) and n not in (6, 8)),
                     WINDOW_COUNTS[-1])
        self._set_windows(count)
        self._slots = ids[:count] + [None] * (count - len(ids[:count]))
        self._render()

    def _on_drop(self, slot: int, camera_id: int, from_slot: int):
        if from_slot >= 0 and from_slot < len(self._slots):
            self._go_custom()
            self._slots[from_slot], self._slots[slot] = self._slots[slot], self._slots[from_slot]
            self._render()
        else:
            self.open_in_window(camera_id, slot)

    def close_window(self, camera_id: int):
        if self._single == camera_id:
            self._single = None
        if camera_id in self._slots:
            self._manual = True
            self._btn_tour.setChecked(False)
            self._slots[self._slots.index(camera_id)] = None
        self._render()
        if camera_id not in self._pinned and camera_id not in self._grid.visible_ids():
            self.sm.stop_stream(camera_id)
            self._connected.discard(camera_id)
            self._status.pop(camera_id, None)
            self._tree_state(camera_id)
            self._emit_count()

    def close_all_windows(self):
        for cid in [s for s in self._slots if s is not None]:
            self.close_window(cid)

    # ------------------------------------------------------------- views
    def _views(self) -> Dict[str, Dict]:
        views = getattr(Config, "LIVE_VIEWS", None)
        return views if isinstance(views, dict) else {}

    def _fill_views_menu(self):
        m = self._views_menu
        m.clear()
        m.addAction("Save current view…").triggered.connect(self._save_view_dialog)
        views = self._views()
        if views:
            m.addSeparator()
            for name in sorted(views):
                m.addAction(name).triggered.connect(lambda _c=False, n=name: self.load_view(n))
            sub = m.addMenu("Delete view")
            for name in sorted(views):
                sub.addAction(name).triggered.connect(lambda _c=False, n=name: self.delete_view(n))

    def _save_view_dialog(self):
        name, ok = QInputDialog.getText(self, "Save view", "Name for this view:")
        if ok and name.strip():
            self.save_view(name.strip())

    def save_view(self, name: str):
        views = dict(self._views())
        self._fit_slots()
        views[name] = {"windows": self._grid.page_size, "slots": list(self._slots)}
        Config.LIVE_VIEWS = views
        self._persist()
        self.message.emit(f"View saved: {name}")

    def load_view(self, name: str) -> bool:
        view = self._views().get(name)
        if not view:
            return False
        count = int(view.get("windows") or 4)
        if count not in WINDOW_COUNTS:
            count = 4
        self._go_custom()
        self._set_windows(count)
        self._slots = [s if s in self._by_id else None for s in view.get("slots", [])]
        self._render()
        return True

    def delete_view(self, name: str):
        views = dict(self._views())
        views.pop(name, None)
        Config.LIVE_VIEWS = views
        self._persist()

    @staticmethod
    def _persist():
        try:
            Config.save_settings()
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Could not save views: %s", e)

    # --------------------------------------------------------- auto-switch
    def _toggle_tour(self, on: bool):
        if on:
            if self._manual:
                self._manual = False
                self._fill_page()
                self._render()
            self._tour.start(self._tour_cb.currentData() * 1000)
        else:
            self._tour.stop()

    def _tour_step(self):
        if self._manual or self._single is not None:
            return
        self._page = (self._page + 1) % self._pages()
        self._fill_page()
        self._render()

    # ---------------------------------------------------------------- streams
    def start_camera(self, camera_id: int, pin: bool = False) -> bool:
        cam = self._by_id.get(camera_id)
        if not cam or not cam.get("rtsp_url"):
            return False
        if pin:
            self._pinned.add(camera_id)
        self.sm.start_stream(cam["id"], cam["rtsp_url"],
                             on_frame=self._grid.update_frame, on_status=self._on_status,
                             transport=cam.get("rtsp_transport") or "auto",
                             keyframe_request=keyframe_requester(cam),
                             snapshot=snapshot_fetcher(cam))
        self.tree.set_state(camera_id, "connecting")
        self._emit_count()
        return True

    def start_all_streams(self):
        # Stream the cameras on screen, a few at a time: many NVRs reject a
        # burst of simultaneous RTSP requests or limit how many run at once.
        self._live = True
        self._sync_streams()

    def _start_next(self):
        if not self._start_queue:
            return
        cid = self._start_queue.pop(0)
        if not self.sm.is_streaming(cid):
            self.start_camera(cid)
        QTimer.singleShot(400, self._start_next)

    def _on_status(self, cid: int, ok: bool, msg: str):
        self._status[cid] = (ok, msg)
        self._grid.update_status(cid, ok, msg)
        if ok:
            self._connected.add(cid)
        else:
            self._connected.discard(cid)
        self._tree_state(cid)
        self._emit_count()

    def _tree_state(self, cid: int):
        if cid in self._connected:
            state = "live"
        elif not self.sm.is_streaming(cid):
            state = "idle"
        else:
            msg = self._status.get(cid, (False, CONNECTING_TEXT))[1]
            state = "connecting" if msg.startswith(("Connecting", "Connected")) else "error"
        self.tree.set_state(cid, state)

    def stop_all_streams(self):
        """Stop live viewing. Cameras that record or run AI keep streaming."""
        self._live = False
        self._btn_tour.setChecked(False)
        self._start_queue.clear()
        for cam in self._cameras:
            if cam["id"] in self._pinned:
                continue
            self._status.pop(cam["id"], None)
            self.sm.stop_stream(cam["id"])
            self._connected.discard(cam["id"])
            self._grid.update_status(cam["id"], False, "Stopped")
            self.tree.set_state(cam["id"], "idle")
        self._emit_count()

    def _emit_count(self):
        self.active_count_changed.emit(
            sum(1 for c in self._cameras
                if c["id"] in self._connected and self.sm.is_streaming(c["id"])))

    def set_recording_state(self, camera_id: int, recording: bool):
        self._recording[camera_id] = recording
        manual = bool(self.recorder and self.recorder.is_manual(camera_id))
        self._grid.set_recording(camera_id, recording, manual)
        if camera_id == self._selected:
            self.ptz.set_manual(manual)

    def connected_ids(self):
        return set(self._connected)

    # --------------------------------------------------------------- events
    def add_event(self, alert: Dict):
        when = alert.get("time") or datetime.now()
        cam = self._by_id.get(alert.get("camera_id"), {})
        text = (f"{when:%H:%M:%S}   {cam.get('name', 'Camera')}   "
                f"{alert.get('type', 'Alert')}: {alert.get('description', '')}")
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, (alert.get("camera_id"), when))
        color = {"high": "#ef4565", "medium": "#f39c12"}.get(alert.get("severity"), "#dde1ec")
        item.setForeground(QColor(color))
        self._event_list.insertItem(0, item)
        while self._event_list.count() > 200:
            self._event_list.takeItem(self._event_list.count() - 1)

    def _on_event_double(self, item):
        cid, when = item.data(Qt.ItemDataRole.UserRole)
        if cid is not None:
            self.playback_requested.emit(cid, when)

    def _instant_playback(self, camera_id: int):
        self.playback_requested.emit(camera_id, datetime.now() - timedelta(seconds=30))

    # --------------------------------------------------------------- diagnose
    def diagnose(self, camera_id: int):
        cam = self._by_id.get(camera_id)
        if cam:
            DiagnoseDialog(cam, self).exec()

    # ---------------------------------------------------------------- snapshot
    def capture_all(self):
        ids = [cid for cid in self._grid.visible_ids() if cid in self._connected]
        if not ids:
            self.message.emit("No live windows to capture.")
        for cid in ids:
            self._take_snapshot(cid)

    def _take_snapshot(self, camera_id: int):
        cam = self._by_id.get(camera_id)
        if not cam or not cam.get("rtsp_url"):
            return

        live = self.sm.latest_frame(camera_id)

        def grab(url):
            # Reuse the running stream: many cameras limit simultaneous connections.
            frame = live if live is not None else StreamManager.snapshot(url)
            if frame is None:
                return None
            os.makedirs(Config.SNAPSHOTS_DIR, exist_ok=True)
            path = os.path.join(Config.SNAPSHOTS_DIR,
                                f"snap_{camera_id}_{datetime.now():%Y%m%d_%H%M%S}.jpg")
            cv2.imwrite(path, frame)
            return path

        run_async(grab, lambda path, err: self.message.emit(
            f"Snapshot saved: {path}" if path else "Snapshot failed"),
            cam["rtsp_url"], receiver=self)


class DiagnoseDialog(QDialog):
    """Runs the connection test in the background and shows a copyable report."""

    def __init__(self, cam: Dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Diagnose: {cam['name']}")
        self.resize(760, 520)
        lay = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setStyleSheet("font-family: Consolas, 'DejaVu Sans Mono', monospace;")
        self.text.setPlainText("Testing the connection (network, login, then video over TCP "
                               "and UDP). This takes up to 30 seconds…")
        lay.addWidget(self.text)
        row = QHBoxLayout()
        self.copy_btn = QPushButton("Copy report")
        self.copy_btn.setObjectName("primary_btn")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addStretch()
        row.addWidget(self.copy_btn)
        row.addWidget(close)
        lay.addLayout(row)
        from app.core.diagnose import run_diagnosis
        run_async(run_diagnosis, self._done, cam, receiver=self)

    def _done(self, report, error):
        self.text.setPlainText(report if report else f"Diagnosis failed: {error}")
        self.copy_btn.setEnabled(bool(report))

    def _copy(self):
        QApplication.clipboard().setText(self.text.toPlainText())
        self.copy_btn.setText("Copied")
