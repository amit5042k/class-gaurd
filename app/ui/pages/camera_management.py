from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialog,
    QFormLayout, QLineEdit, QComboBox, QSpinBox, QCheckBox,
    QGroupBox, QGridLayout, QMessageBox, QDialogButtonBox
)
from PyQt6.QtCore import Qt
from app.core.camera_manager import CameraManager
from app.config import Config
from app.protocols.onvif_handler import ONVIFHandler


COLS = ["ID", "Name", "Brand", "Type", "Host", "Port",
        "Channel", "Group", "Connection", "Status", "Actions"]


class CameraDialog(QDialog):
    def __init__(self, data: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Camera" if not data else "Edit Camera")
        self.setMinimumWidth(540)
        self._data = data or {}
        self._build_ui()
        if data:
            self._populate(data)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        def lbl(t): l = QLabel(t); l.setObjectName("form_label"); return l

        self._name     = QLineEdit(); self._name.setPlaceholderText("Camera name")
        self._brand    = QComboBox(); self._brand.addItems(Config.CAMERA_BRANDS)
        self._cam_type = QComboBox(); self._cam_type.addItems(Config.CAMERA_TYPES)
        self._conn     = QComboBox(); self._conn.addItems(Config.CONN_TYPES)
        self._host     = QLineEdit(); self._host.setPlaceholderText("192.168.1.100")
        self._port     = QSpinBox(); self._port.setRange(1, 65535); self._port.setValue(554)
        self._http     = QSpinBox(); self._http.setRange(1, 65535); self._http.setValue(80)
        self._user     = QLineEdit(); self._user.setText("admin")
        self._pwd      = QLineEdit(); self._pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self._ch       = QSpinBox(); self._ch.setRange(1, 32); self._ch.setValue(1)
        self._loc      = QLineEdit(); self._loc.setPlaceholderText("e.g. Main Gate")
        self._group    = QLineEdit(); self._group.setText("Default")
        self._rtsp     = QLineEdit(); self._rtsp.setPlaceholderText("Leave blank to auto-build")

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
        form.addRow(lbl("Location:"), self._loc)
        form.addRow(lbl("Group:"), self._group)
        form.addRow(lbl("Custom RTSP URL:"), self._rtsp)
        layout.addLayout(form)

        # AI features
        gb = QGroupBox("AI Features")
        gl = QGridLayout(gb)
        self._face_det  = QCheckBox("Face Detection")
        self._attend    = QCheckBox("Attendance Tracking")
        self._expr      = QCheckBox("Expression Monitor")
        self._safety    = QCheckBox("Safety Watch")
        self._rec       = QCheckBox("Auto Recording")
        for i, cb in enumerate([self._face_det, self._attend,
                                 self._expr, self._safety, self._rec]):
            gl.addWidget(cb, i // 2, i % 2)
        layout.addWidget(gb)

        # Test + buttons
        btn_row = QHBoxLayout()
        test_btn = QPushButton("Test Connection")
        test_btn.setObjectName("secondary_btn")
        test_btn.clicked.connect(self._test_conn)
        btn_row.addWidget(test_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self, d: dict):
        self._name.setText(d.get("name", ""))
        idx = self._brand.findText(d.get("brand", ""))
        if idx >= 0: self._brand.setCurrentIndex(idx)
        idx = self._cam_type.findText(d.get("camera_type", ""))
        if idx >= 0: self._cam_type.setCurrentIndex(idx)
        idx = self._conn.findText(d.get("connection_type", ""))
        if idx >= 0: self._conn.setCurrentIndex(idx)
        self._host.setText(d.get("host", ""))
        self._port.setValue(d.get("port", 554))
        self._http.setValue(d.get("http_port", 80))
        self._user.setText(d.get("username", "admin"))
        self._pwd.setText(d.get("password", ""))
        self._ch.setValue(d.get("channel", 1))
        self._loc.setText(d.get("location", ""))
        self._group.setText(d.get("group_name", "Default"))
        self._rtsp.setText(d.get("rtsp_url", ""))
        self._face_det.setChecked(bool(d.get("face_detection")))
        self._attend.setChecked(bool(d.get("attendance_tracking")))
        self._expr.setChecked(bool(d.get("expression_monitor")))
        self._safety.setChecked(bool(d.get("safety_watch")))
        self._rec.setChecked(bool(d.get("recording_enabled")))

    def _test_conn(self):
        import threading
        from app.protocols.generic_rtsp import RTSPProbe
        rtsp = self._rtsp.text().strip() or Config.build_rtsp_url(
            self._brand.currentText(), self._host.text(),
            self._port.value(), self._user.text(), self._pwd.text(),
            self._ch.value()
        )
        def run():
            ok = RTSPProbe(rtsp).test_connection()
            QMessageBox.information(self, "Test",
                f"{'Connected' if ok else 'Failed'}\n{rtsp}")
        threading.Thread(target=run, daemon=True).start()

    def get_data(self) -> dict:
        return {
            "name":             self._name.text().strip(),
            "brand":            self._brand.currentText(),
            "camera_type":      self._cam_type.currentText(),
            "connection_type":  self._conn.currentText(),
            "host":             self._host.text().strip(),
            "port":             self._port.value(),
            "http_port":        self._http.value(),
            "username":         self._user.text(),
            "password":         self._pwd.text(),
            "channel":          self._ch.value(),
            "location":         self._loc.text(),
            "group_name":       self._group.text() or "Default",
            "rtsp_url":         self._rtsp.text().strip() or None,
            "face_detection":   self._face_det.isChecked(),
            "attendance_tracking": self._attend.isChecked(),
            "expression_monitor":  self._expr.isChecked(),
            "safety_watch":     self._safety.isChecked(),
            "recording_enabled": self._rec.isChecked(),
        }


class CameraManagement(QWidget):
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
        btn_add.clicked.connect(self._add)
        hdr.addWidget(btn_discover)
        hdr.addWidget(btn_add)
        root.addLayout(hdr)

        self._table = QTableWidget(0, len(COLS))
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
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
                str(cam["id"]), cam["name"], cam["brand"],
                cam["camera_type"], cam["host"],
                str(cam["port"]), str(cam["channel"]),
                cam["group_name"], cam["connection_type"],
                "Active" if cam["is_active"] else "Inactive"
            ]):
                item = QTableWidgetItem(val)
                item.setData(Qt.ItemDataRole.UserRole, cam["id"])
                self._table.setItem(r, c, item)

            # Actions
            actions = QWidget()
            al = QHBoxLayout(actions)
            al.setContentsMargins(4, 2, 4, 2)
            al.setSpacing(4)
            edit_btn = QPushButton("Edit")
            edit_btn.setObjectName("secondary_btn")
            edit_btn.setFixedWidth(50)
            del_btn = QPushButton("Del")
            del_btn.setObjectName("danger_btn")
            del_btn.setFixedWidth(40)
            cid = cam["id"]
            edit_btn.clicked.connect(lambda _, i=cid: self._edit(i))
            del_btn.clicked.connect(lambda _, i=cid: self._delete(i))
            al.addWidget(edit_btn)
            al.addWidget(del_btn)
            self._table.setCellWidget(r, len(COLS)-1, actions)

    def _add(self):
        dlg = CameraDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            if not data["name"] or not data["host"]:
                QMessageBox.warning(self, "Error", "Name and host are required.")
                return
            try:
                self.mgr.add_camera(data)
                self.refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _edit(self, camera_id: int):
        cam = self.mgr.get_camera(camera_id)
        if not cam:
            return
        dlg = CameraDialog(data=cam, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self.mgr.update_camera(camera_id, dlg.get_data())
                self.refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _delete(self, camera_id: int):
        if QMessageBox.question(
            self, "Confirm", "Delete this camera?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            self.mgr.delete_camera(camera_id)
            self.refresh()

    def _discover(self):
        import threading
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem
        dlg = QDialog(self)
        dlg.setWindowTitle("ONVIF Discovery")
        dlg.setMinimumSize(500, 350)
        vl = QVBoxLayout(dlg)
        vl.addWidget(QLabel("Scanning network for ONVIF cameras..."))
        lst = QListWidget()
        lst.setStyleSheet("background:#1b1b30; color:#dde1ec;")
        vl.addWidget(lst)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        vl.addWidget(bb)

        def run():
            found = ONVIFHandler.discover(timeout=6)
            for f in found:
                lst.addItem(f["url"])
            if not found:
                lst.addItem("No cameras found.")

        threading.Thread(target=run, daemon=True).start()
        dlg.exec()
