import json
import os
from datetime import datetime
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget
)

from app.core.security import audit
from app.config import Config
from app.ui.widgets.zone_canvas import VideoCanvas
from app.ui.styles import style_table


def load_zones(db, camera_id: int) -> List[List[List[int]]]:
    from app.models import Zone
    session = db.get_session()
    try:
        return [json.loads(z.points) for z in
                session.query(Zone).filter_by(camera_id=camera_id).order_by(Zone.id)]
    finally:
        session.close()


def load_all_zones(db) -> Dict[int, List[List[List[int]]]]:
    from app.models import Zone
    session = db.get_session()
    try:
        out: Dict[int, list] = {}
        for z in session.query(Zone).order_by(Zone.id):
            out.setdefault(z.camera_id, []).append(json.loads(z.points))
        return out
    finally:
        session.close()


class SafetyWatch(QWidget):
    monitor_changed = pyqtSignal(str, object)        # "safety", camera_id or None
    zones_changed = pyqtSignal(int, list)            # camera_id, list of polygons
    model_requested = pyqtSignal(dict)               # {} = unload

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._cameras: List[Dict] = []
        self._active: Optional[int] = None
        self._totals = {"det": 0, "vio": 0, "zone": 0}
        self._build_ui()
        self._update_model_label()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("Safety Watch")
        title.setObjectName("page_title")
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(QLabel("Camera:"))
        self._cam_cb = QComboBox()
        self._cam_cb.setMinimumWidth(180)
        hdr.addWidget(self._cam_cb)
        self._btn = QPushButton("Start Safety Watch")
        self._btn.setObjectName("primary_btn")
        self._btn.clicked.connect(self._toggle)
        hdr.addWidget(self._btn)
        root.addLayout(hdr)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self._video = VideoCanvas("Select a camera and click Start Safety Watch")
        self._video.point_added.connect(self._on_point)
        ll.addWidget(self._video, 1)

        zone_box = QGroupBox("Restricted zones")
        zl = QHBoxLayout(zone_box)
        self._zone_info = QLabel("Start Safety Watch to draw zones.")
        self._zone_info.setWordWrap(True)
        self._btn_draw = QPushButton("Draw zone")
        self._btn_draw.setObjectName("secondary_btn")
        self._btn_draw.clicked.connect(self._start_zone)
        self._btn_finish = QPushButton("Save zone")
        self._btn_finish.setObjectName("success_btn")
        self._btn_finish.clicked.connect(self._finish_zone)
        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.clicked.connect(self._cancel_zone)
        self._btn_clear = QPushButton("Delete all zones")
        self._btn_clear.setObjectName("danger_btn")
        self._btn_clear.clicked.connect(self._clear_zones)
        zl.addWidget(self._zone_info, 1)
        for b in (self._btn_draw, self._btn_finish, self._btn_cancel, self._btn_clear):
            zl.addWidget(b)
        ll.addWidget(zone_box)

        model_box = QGroupBox("Detection model")
        ml = QHBoxLayout(model_box)
        self._model_lbl = QLabel()
        self._model_lbl.setWordWrap(True)
        self._model_lbl.setStyleSheet("color:#9098b1; font-size:11px;")
        btn_load = QPushButton("Load model…")
        btn_load.setObjectName("secondary_btn")
        btn_load.clicked.connect(self._load_model)
        btn_unload = QPushButton("Use built-in")
        btn_unload.clicked.connect(self._unload_model)
        ml.addWidget(self._model_lbl, 1)
        ml.addWidget(btn_load)
        ml.addWidget(btn_unload)
        ll.addWidget(model_box)
        splitter.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("Violations on this camera"))
        self._table = QTableWidget(0, 4)
        style_table(self._table)
        self._table.setHorizontalHeaderLabels(["Time", "Type", "Confidence", "In zone"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        rl.addWidget(self._table)
        stats = QHBoxLayout()
        self._lbl_det = QLabel()
        self._lbl_vio = QLabel()
        self._lbl_zone = QLabel()
        for w in (self._lbl_det, self._lbl_vio, self._lbl_zone):
            stats.addWidget(w)
        stats.addStretch()
        rl.addLayout(stats)
        splitter.addWidget(right)
        splitter.setSizes([620, 320])
        root.addWidget(splitter, 1)
        self._update_stats()
        self._update_zone_buttons()

    # --------------------------------------------------------------- inputs
    def set_cameras(self, cameras: List[Dict]):
        current = self._cam_cb.currentData()
        self._cameras = cameras
        self._cam_cb.blockSignals(True)
        self._cam_cb.clear()
        for c in cameras:
            self._cam_cb.addItem(c["name"], c["id"])
        idx = self._cam_cb.findData(current)
        self._cam_cb.setCurrentIndex(max(idx, 0))
        self._cam_cb.blockSignals(False)
        if self._active is not None and self._active not in {c["id"] for c in cameras}:
            self._stop()

    def _toggle(self):
        if self._active is None:
            cid = self._cam_cb.currentData()
            if cid is None:
                self._video.clear_image("Add a camera first (Camera Management).")
                return
            self._active = cid
            self._totals = {"det": 0, "vio": 0, "zone": 0}
            self._table.setRowCount(0)
            self._cam_cb.setEnabled(False)
            self._btn.setText("Stop Safety Watch")
            self._video.clear_image("Connecting to the camera…")
            self.monitor_changed.emit("safety", cid)
        else:
            self._stop()
        self._update_stats()
        self._update_zone_buttons()

    def _stop(self):
        if self._video.drawing:
            self._video.stop_drawing()
        self._active = None
        self._cam_cb.setEnabled(True)
        self._btn.setText("Start Safety Watch")
        self._video.clear_image("Safety Watch stopped")
        self.monitor_changed.emit("safety", None)
        self._update_zone_buttons()

    def on_frame(self, kind: str, camera_id: int, img: QImage):
        if kind == "safety" and camera_id == self._active:
            first = not self._video.has_image()
            self._video.set_image(img)
            if first:
                self._update_zone_buttons()

    def on_results(self, camera_id: int, detections: list):
        if camera_id != self._active:
            return
        self._totals["det"] += len(detections)
        for d in detections:
            if d.get("violation") or d.get("zone_intrusion"):
                self._totals["vio"] += 1 if d.get("violation") else 0
                self._totals["zone"] += 1 if d.get("zone_intrusion") else 0
                self._log(d)
        self._update_stats()

    def _log(self, d: Dict):
        if self._table.rowCount() >= 300:
            self._table.removeRow(0)
        r = self._table.rowCount()
        self._table.insertRow(r)
        vals = [datetime.now().strftime("%H:%M:%S"), d.get("class", ""),
                f"{d.get('confidence', 0):.0%}", "Yes" if d.get("zone_intrusion") else "No"]
        for c, v in enumerate(vals):
            item = QTableWidgetItem(v)
            item.setForeground(QColor("#ef4565"))
            self._table.setItem(r, c, item)
        self._table.scrollToBottom()

    def _update_stats(self):
        self._lbl_det.setText(f"Detections: {self._totals['det']}")
        self._lbl_vio.setText(f"PPE violations: {self._totals['vio']}")
        self._lbl_zone.setText(f"Zone intrusions: {self._totals['zone']}")

    # ----------------------------------------------------------------- zones
    def _update_zone_buttons(self):
        live = self._active is not None and self._video.has_image()
        drawing = self._video.drawing
        n = len(load_zones(self.db, self._active)) if self._active is not None else 0
        self._btn_draw.setEnabled(live and not drawing)
        self._btn_finish.setEnabled(drawing)
        self._btn_cancel.setEnabled(drawing)
        self._btn_clear.setEnabled(self._active is not None and n > 0 and not drawing)
        if drawing:
            self._zone_info.setText("Click points around the area on the picture, "
                                    "then click Save zone (at least 3 points).")
        elif self._active is None:
            self._zone_info.setText("Start Safety Watch to draw zones.")
        else:
            self._zone_info.setText(f"{n} zone(s) on this camera. A person whose feet "
                                    "are inside a zone triggers a high-priority alert.")

    def _start_zone(self):
        self._video.start_drawing()
        self._update_zone_buttons()

    def _on_point(self, count: int):
        self._btn_finish.setEnabled(count >= 3)

    def _cancel_zone(self):
        self._video.stop_drawing()
        self._update_zone_buttons()

    def _finish_zone(self):
        pts = self._video.stop_drawing()
        if len(pts) < 3 or self._active is None:
            QMessageBox.information(self, "Zone", "A zone needs at least 3 points.")
            self._update_zone_buttons()
            return
        from app.models import Zone
        session = self.db.get_session()
        try:
            session.add(Zone(camera_id=self._active, points=json.dumps(pts)))
            session.commit()
        finally:
            session.close()
        audit(self.db, "zone_add", f"Camera id {self._active}")
        self.zones_changed.emit(self._active, load_zones(self.db, self._active))
        self._update_zone_buttons()

    def _clear_zones(self):
        if self._active is None:
            return
        if QMessageBox.question(self, "Delete zones", "Delete all zones on this camera?",
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        from app.models import Zone
        session = self.db.get_session()
        try:
            session.query(Zone).filter_by(camera_id=self._active).delete()
            session.commit()
        finally:
            session.close()
        audit(self.db, "zone_clear", f"Camera id {self._active}")
        self.zones_changed.emit(self._active, [])
        self._update_zone_buttons()

    # ----------------------------------------------------------------- model
    def _update_model_label(self, status: str = ""):
        m = Config.SAFETY_MODEL or {}
        if m.get("weights"):
            text = f"Model: {os.path.basename(m['weights'])}"
        else:
            text = ("Built-in person detector. For helmet / mask / vest checks, load a "
                    "YOLO model (.onnx, or Darknet .weights + .cfg) with a class-names file.")
        self._model_lbl.setText(text + (f"\n{status}" if status else ""))

    def on_model_status(self, message: str):
        failed = not message.startswith(("Model loaded", "No model"))
        if failed:
            Config.SAFETY_MODEL = {}
            Config.save_settings()
        self._update_model_label(message)

    def _load_model(self):
        weights, _ = QFileDialog.getOpenFileName(
            self, "Select model", "", "Models (*.onnx *.weights);;All files (*)")
        if not weights:
            return
        cfg = ""
        if weights.lower().endswith(".weights"):
            cfg, _ = QFileDialog.getOpenFileName(self, "Select Darknet .cfg", "", "Config (*.cfg)")
            if not cfg:
                return
        names, _ = QFileDialog.getOpenFileName(
            self, "Select class names file (one class per line)", "",
            "Names (*.names *.txt);;All files (*)")
        if not names:
            return
        Config.SAFETY_MODEL = {"weights": weights, "cfg": cfg, "names": names}
        Config.save_settings()
        audit(self.db, "model_load", os.path.basename(weights))
        self._update_model_label("Loading…")
        self.model_requested.emit(dict(Config.SAFETY_MODEL))

    def _unload_model(self):
        Config.SAFETY_MODEL = {}
        Config.save_settings()
        self._update_model_label()
        self.model_requested.emit({})
