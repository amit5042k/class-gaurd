from urllib.parse import urlparse

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget
)

from app.core.security import audit
from app.config import Config
from app.core.camera_manager import CameraManager
from app.protocols.generic_rtsp import RTSPProbe
from app.protocols.onvif_handler import ONVIFHandler
from app.ui.async_task import run_async
from app.ui.styles import style_table

COLS = ["ID", "Name", "Brand", "Type", "Host", "Port",
        "Channel", "Group", "Connection", "Status", "Actions"]


class CameraDialog(QDialog):
    def __init__(self, data: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Camera" if data and data.get("id") else "Add Camera")
        self.setMinimumWidth(560)
        self._build_ui()
        if data:
            self._populate(data)
        self._update_url_hint()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        def lbl(t):
            label = QLabel(t)
            label.setObjectName("form_label")
            return label

        self._name = QLineEdit()
        self._name.setPlaceholderText("Camera name")
        self._brand = QComboBox()
        self._brand.addItems(Config.CAMERA_BRANDS)
        self._cam_type = QComboBox()
        self._cam_type.addItems(Config.CAMERA_TYPES)
        self._conn = QComboBox()
        self._conn.addItems(Config.CONN_TYPES)
        self._host = QLineEdit()
        self._host.setPlaceholderText("192.168.1.100")
        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(554)
        self._http = QSpinBox()
        self._http.setRange(1, 65535)
        self._http.setValue(80)
        self._user = QLineEdit("admin")
        self._pwd = QLineEdit()
        self._pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self._ch = QSpinBox()
        self._ch.setRange(1, 128)
        self._ch.setValue(1)
        self._sub = QCheckBox("Use sub-stream (lower quality, less bandwidth; good for Wi-Fi and big grids)")
        self._active = QCheckBox("Camera enabled")
        self._active.setChecked(True)
        self._loc = QLineEdit()
        self._loc.setPlaceholderText("e.g. Main Gate")
        self._group = QLineEdit("Default")
        self._rtsp = QLineEdit()
        self._url_hint = QLabel()
        self._url_hint.setWordWrap(True)
        self._url_hint.setStyleSheet("color:#777; font-size:11px;")

        form.addRow(lbl("Name:"), self._name)
        form.addRow(lbl("Brand:"), self._brand)
        form.addRow(lbl("Type:"), self._cam_type)
        form.addRow(lbl("Connection:"), self._conn)
        form.addRow(lbl("Host / IP:"), self._host)
        form.addRow(lbl("RTSP Port:"), self._port)
        form.addRow(lbl("HTTP Port:"), self._http)
        form.addRow(lbl("Username:"), self._user)
        form.addRow(lbl("Password:"), self._pwd)
        form.addRow(lbl("Channel:"), self._ch)
        form.addRow(lbl(""), self._sub)
        self._transport = QComboBox()
        for label, val in (("Auto (TCP, falls back to UDP)", "auto"), ("TCP", "tcp"),
                           ("UDP", "udp")):
            self._transport.addItem(label, val)
        form.addRow(lbl("RTSP transport:"), self._transport)
        form.addRow(lbl("Location:"), self._loc)
        form.addRow(lbl("Group:"), self._group)
        rtsp_row = QHBoxLayout()
        rtsp_row.addWidget(self._rtsp, 1)
        self._btn_onvif = QPushButton("Get from camera (ONVIF)")
        self._btn_onvif.setObjectName("secondary_btn")
        self._btn_onvif.setToolTip("Ask the camera for its stream address over ONVIF "
                                   "(uses the HTTP port)")
        self._btn_onvif.clicked.connect(self._fetch_onvif)
        rtsp_row.addWidget(self._btn_onvif)
        form.addRow(lbl("Custom RTSP URL:"), rtsp_row)
        form.addRow(lbl(""), self._url_hint)
        form.addRow(lbl(""), self._active)
        layout.addLayout(form)

        for w in (self._brand,):
            w.currentIndexChanged.connect(self._update_url_hint)
        for w in (self._host, self._user, self._pwd, self._rtsp):
            w.textChanged.connect(self._update_url_hint)
        for w in (self._port, self._ch):
            w.valueChanged.connect(self._update_url_hint)
        self._sub.toggled.connect(self._update_url_hint)

        gb = QGroupBox("AI Features")
        gl = QGridLayout(gb)
        self._face_det = QCheckBox("Face Recognition")
        self._attend = QCheckBox("Attendance Tracking")
        self._expr = QCheckBox("Expression Monitor")
        self._safety = QCheckBox("Safety Watch")
        for i, cb in enumerate([self._face_det, self._attend, self._expr, self._safety]):
            gl.addWidget(cb, i // 2, i % 2)
        layout.addWidget(gb)

        rb = QGroupBox("Recording")
        rl = QHBoxLayout(rb)
        self._rec_mode = QComboBox()
        for label, mode in (("Off", "off"), ("Continuous (24×7)", "continuous"),
                            ("Only when motion is detected", "motion")):
            self._rec_mode.addItem(label, mode)
        rl.addWidget(QLabel("Record:"))
        rl.addWidget(self._rec_mode, 1)
        layout.addWidget(rb)

        btn_row = QHBoxLayout()
        self._test_btn = QPushButton("Test Connection")
        self._test_btn.setObjectName("secondary_btn")
        self._test_btn.clicked.connect(self._test_conn)
        self._test_result = QLabel()
        self._test_result.setWordWrap(True)
        btn_row.addWidget(self._test_btn)
        btn_row.addWidget(self._test_result, 1)
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_combo(self, combo: QComboBox, text: str):
        idx = combo.findText(text or "")
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _populate(self, d: dict):
        self._name.setText(d.get("name", ""))
        self._set_combo(self._brand, d.get("brand"))
        self._set_combo(self._cam_type, d.get("camera_type"))
        self._set_combo(self._conn, d.get("connection_type"))
        self._host.setText(d.get("host", ""))
        self._port.setValue(d.get("port") or 554)
        self._http.setValue(d.get("http_port") or 80)
        self._user.setText(d.get("username", "admin"))
        self._pwd.setText(d.get("password", ""))
        self._ch.setValue(d.get("channel") or 1)
        self._sub.setChecked(bool(d.get("use_substream")))
        self._transport.setCurrentIndex(max(0, self._transport.findData(
            d.get("rtsp_transport") or "auto")))
        self._active.setChecked(d.get("is_active", True) is not False)
        self._loc.setText(d.get("location", ""))
        self._group.setText(d.get("group_name", "Default"))
        # Only a custom URL is shown here; automatic ones are rebuilt from the fields.
        if "custom_rtsp_url" in d:
            self._rtsp.setText(d["custom_rtsp_url"] or "")
        else:
            stored = d.get("rtsp_url") or ""
            if stored and d.get("host") and stored != CameraManager.auto_url(d):
                self._rtsp.setText(stored)
        self._face_det.setChecked(bool(d.get("face_detection")))
        self._attend.setChecked(bool(d.get("attendance_tracking")))
        self._expr.setChecked(bool(d.get("expression_monitor")))
        self._safety.setChecked(bool(d.get("safety_watch")))
        idx = self._rec_mode.findData(d.get("recording_mode") or "off")
        self._rec_mode.setCurrentIndex(max(0, idx))

    def _auto_url(self) -> str:
        return Config.build_rtsp_url(
            self._brand.currentText(), self._host.text().strip() or "<ip>",
            self._port.value(), self._user.text(), self._pwd.text(),
            self._ch.value(), self._sub.isChecked())

    def _effective_url(self) -> str:
        return self._rtsp.text().strip() or self._auto_url()

    def _update_url_hint(self):
        if self._rtsp.text().strip():
            self._url_hint.setText("Using your custom URL.")
        else:
            shown = Config.build_rtsp_url(
                self._brand.currentText(), self._host.text().strip() or "<ip>",
                self._port.value(), self._user.text(), "****" if self._pwd.text() else "",
                self._ch.value(), self._sub.isChecked())
            self._url_hint.setText(f"Leave blank to use: {shown}")

    def _test_conn(self):
        if not self._host.text().strip() and not self._rtsp.text().strip():
            self._test_result.setText("Enter the camera's IP address first.")
            return
        self._test_btn.setEnabled(False)
        self._test_result.setStyleSheet("color:#9098b1;")
        self._test_result.setText("Testing… (up to 10 seconds)")
        run_async(lambda url: RTSPProbe(url).test_connection(),
                  self._on_test_done, self._effective_url(), receiver=self)

    def _on_test_done(self, result, error):
        self._test_btn.setEnabled(True)
        ok, msg = result if result else (False, str(error))
        self._test_result.setStyleSheet("color:#27ae60;" if ok else "color:#ef4565;")
        self._test_result.setText(("Connected. " if ok else "Failed. ") + msg)

    def _fetch_onvif(self):
        host = self._host.text().strip()
        if not host:
            self._test_result.setText("Enter the camera's IP address first.")
            return
        self._btn_onvif.setEnabled(False)
        self._test_result.setStyleSheet("color:#9098b1;")
        self._test_result.setText("Asking the camera over ONVIF…")

        def fetch(h, port, user, pwd, sub):
            handler = ONVIFHandler(h, port, user, pwd)
            if not handler.connect():
                raise ConnectionError("ONVIF connection failed (check HTTP port, login, "
                                      "and that ONVIF is enabled on the camera)")
            uri = handler.get_stream_uri(1 if sub else 0) or handler.get_stream_uri(0)
            if not uri:
                raise ConnectionError("The camera did not return a stream address")
            return uri

        run_async(fetch, self._on_onvif, host, self._http.value(), self._user.text(),
                  self._pwd.text(), self._sub.isChecked(), receiver=self)

    def _on_onvif(self, uri, error):
        self._btn_onvif.setEnabled(True)
        if error:
            self._test_result.setStyleSheet("color:#ef4565;")
            self._test_result.setText(str(error))
            return
        self._rtsp.setText(uri)
        self._test_result.setStyleSheet("color:#27ae60;")
        self._test_result.setText("Stream address received from the camera.")

    def _accept(self):
        if not self._name.text().strip() or not (self._host.text().strip() or
                                                 self._rtsp.text().strip()):
            QMessageBox.warning(self, "Missing details", "Name and host / IP are required.")
            return
        self.accept()

    def get_data(self) -> dict:
        host = self._host.text().strip()
        custom = self._rtsp.text().strip()
        if not host and custom:
            host = urlparse(custom).hostname or custom
        return {
            "name": self._name.text().strip(),
            "brand": self._brand.currentText(),
            "camera_type": self._cam_type.currentText(),
            "connection_type": self._conn.currentText(),
            "host": host,
            "port": self._port.value(),
            "http_port": self._http.value(),
            "username": self._user.text(),
            "password": self._pwd.text(),
            "channel": self._ch.value(),
            "use_substream": self._sub.isChecked(),
            "rtsp_transport": self._transport.currentData(),
            "is_active": self._active.isChecked(),
            "location": self._loc.text(),
            "group_name": self._group.text() or "Default",
            "rtsp_url": custom or None,
            "face_detection": self._face_det.isChecked(),
            "attendance_tracking": self._attend.isChecked(),
            "expression_monitor": self._expr.isChecked(),
            "safety_watch": self._safety.isChecked(),
            "recording_mode": self._rec_mode.currentData(),
        }


class DiscoveryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ONVIF Discovery")
        self.setMinimumSize(520, 360)
        self.selected_host = None
        vl = QVBoxLayout(self)
        self._status = QLabel("Scanning the local network for ONVIF cameras…")
        vl.addWidget(self._status)
        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _: self._use())
        vl.addWidget(self._list)
        row = QHBoxLayout()
        self._rescan = QPushButton("Scan again")
        self._rescan.setObjectName("secondary_btn")
        self._rescan.clicked.connect(self._scan)
        self._use_btn = QPushButton("Add selected camera")
        self._use_btn.setObjectName("primary_btn")
        self._use_btn.setEnabled(False)
        self._use_btn.clicked.connect(self._use)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(self._rescan)
        row.addStretch()
        row.addWidget(self._use_btn)
        row.addWidget(close)
        vl.addLayout(row)
        self._list.currentItemChanged.connect(
            lambda cur, _: self._use_btn.setEnabled(cur is not None and
                                                    cur.data(Qt.ItemDataRole.UserRole) is not None))
        self._scan()

    def _scan(self):
        self._list.clear()
        self._rescan.setEnabled(False)
        self._status.setText("Scanning the local network for ONVIF cameras…")
        run_async(ONVIFHandler.discover, self._on_found, 6, receiver=self)

    def _on_found(self, found, error):
        self._rescan.setEnabled(True)
        if error or not found:
            self._status.setText("No cameras found." + (f" ({error})" if error else
                                 " Check the camera is on the same network and ONVIF is enabled."))
            return
        self._status.setText(f"Found {len(found)} device(s). Select one to add it.")
        for f in found:
            host = urlparse(f["url"]).hostname
            item = QListWidgetItem(f"{host}    ({f['url']})")
            item.setData(Qt.ItemDataRole.UserRole, host)
            self._list.addItem(item)

    def _use(self):
        item = self._list.currentItem()
        if item and item.data(Qt.ItemDataRole.UserRole):
            self.selected_host = item.data(Qt.ItemDataRole.UserRole)
            self.accept()


class CameraManagement(QWidget):
    # Emitted after any add / edit / delete so MainWindow can reload
    # the live view, AI worker and page camera lists.
    cameras_changed = pyqtSignal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.mgr = CameraManager(db)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("Camera Management")
        title.setObjectName("page_title")
        hdr.addWidget(title)
        hdr.addStretch()
        btn_discover = QPushButton("ONVIF Discover")
        btn_discover.setObjectName("secondary_btn")
        btn_discover.clicked.connect(self._discover)
        btn_add = QPushButton("+ Add Camera")
        btn_add.setObjectName("primary_btn")
        btn_add.clicked.connect(lambda: self._add())
        hdr.addWidget(btn_discover)
        hdr.addWidget(btn_add)
        root.addLayout(hdr)

        self._table = QTableWidget(0, len(COLS))

        style_table(self._table)
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        root.addWidget(self._table)

    def refresh(self):
        cameras = self.mgr.get_all_cameras()
        self._table.setRowCount(0)
        for cam in cameras:
            r = self._table.rowCount()
            self._table.insertRow(r)
            for c, val in enumerate([
                str(cam["id"]), cam["name"], cam["brand"], cam["camera_type"],
                cam["host"], str(cam["port"]), str(cam["channel"]),
                cam["group_name"], cam["connection_type"],
                "Enabled" if cam["is_active"] else "Disabled",
            ]):
                item = QTableWidgetItem(val)
                item.setData(Qt.ItemDataRole.UserRole, cam["id"])
                self._table.setItem(r, c, item)

            actions = QWidget()
            al = QHBoxLayout(actions)
            al.setContentsMargins(4, 2, 4, 2)
            al.setSpacing(4)
            edit_btn = QPushButton("Edit")
            edit_btn.setObjectName("secondary_btn")
            del_btn = QPushButton("Delete")
            del_btn.setObjectName("danger_btn")
            cid = cam["id"]
            edit_btn.clicked.connect(lambda _, i=cid: self._edit(i))
            del_btn.clicked.connect(lambda _, i=cid: self._delete(i))
            al.addWidget(edit_btn)
            al.addWidget(del_btn)
            self._table.setCellWidget(r, len(COLS) - 1, actions)

    def _add(self, prefill: dict = None):
        dlg = CameraDialog(data=prefill, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            try:
                self.mgr.add_camera(data)
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
                return
            audit(self.db, "camera_add", f"{data['name']} ({data['brand']} {data['host']})")
            self.refresh()
            self.cameras_changed.emit()

    def _edit(self, camera_id: int):
        cam = self.mgr.get_camera(camera_id)
        if not cam:
            return
        dlg = CameraDialog(data=cam, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.mgr.update_camera(camera_id, dlg.get_data())
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
                return
            audit(self.db, "camera_edit", f"{cam['name']} (id {camera_id})")
            self.refresh()
            self.cameras_changed.emit()

    def _delete(self, camera_id: int):
        if QMessageBox.question(
            self, "Confirm", "Delete this camera? Its safety zones are deleted too.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            self.mgr.delete_camera(camera_id)
            audit(self.db, "camera_delete", f"Camera id {camera_id}")
            self.refresh()
            self.cameras_changed.emit()

    def _discover(self):
        dlg = DiscoveryDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_host:
            self._add({"name": f"ONVIF {dlg.selected_host}", "brand": "ONVIF",
                       "host": dlg.selected_host, "port": 554})
